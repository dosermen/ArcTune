"""Read-only телеметрия процессора: загрузка через psutil, частота/температура Core Temp."""

from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class CpuSnapshot:
    """Снимок CPU: загрузка из psutil, частота и температура — из Core Temp (None без него)."""

    usage_percent: float | None
    per_core_usage_percent: tuple[float, ...]
    frequency_mhz: float | None
    temperature_c: float | None = None


@dataclass(frozen=True)
class CoreTempMetrics:
    """Метрики Core Temp из одного прохода по shared memory (None — источника нет)."""

    temperature_c: float | None
    frequency_mhz: float | None


class CpuReadError(RuntimeError):
    """Ошибка получения доступных метрик CPU."""


class CpuSampler:
    """Сэмплирует общую/поточную загрузку CPU без записи настроек.

    Частота из ``psutil.cpu_freq()`` не читается: на части машин Windows
    отдаёт константу (не обновляет ``CurrentMHz``) — её даёт только Core Temp.
    """

    def __init__(self) -> None:
        self._primed = False

    def sample(self) -> CpuSnapshot:
        """Возвращает снимок; загрузка доступна начиная со второго вызова."""
        try:
            per_core = tuple(float(value) for value in psutil.cpu_percent(percpu=True))
        except (psutil.Error, OSError, RuntimeError) as exc:
            raise CpuReadError(f"не удалось прочитать CPU-телеметрию: {exc}") from exc

        if not self._primed:
            self._primed = True
            usage = None
            per_core = ()
        else:
            valid = tuple(
                value for value in per_core if math.isfinite(value) and 0.0 <= value <= 100.0
            )
            per_core = valid
            usage = sum(valid) / len(valid) if valid else None

        return CpuSnapshot(usage, per_core, None)


class _CoreTempSharedDataEx(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("uiLoad", ctypes.c_uint32 * 256),
        ("uiTjMax", ctypes.c_uint32 * 128),
        ("uiCoreCnt", ctypes.c_uint32),
        ("uiCPUCnt", ctypes.c_uint32),
        ("fTemp", ctypes.c_float * 256),
        ("fVID", ctypes.c_float),
        ("fCPUSpeed", ctypes.c_float),
        ("fFSBSpeed", ctypes.c_float),
        ("fMultiplier", ctypes.c_float),
        ("sCPUName", ctypes.c_char * 100),
        ("ucFahrenheit", ctypes.c_uint8),
        ("ucDeltaToTjMax", ctypes.c_uint8),
        ("ucTdpSupported", ctypes.c_uint8),
        ("ucPowerSupported", ctypes.c_uint8),
        ("uiStructVersion", ctypes.c_uint32),
        ("uiTdp", ctypes.c_uint32 * 128),
        ("fPower", ctypes.c_float * 128),
        ("fMultipliers", ctypes.c_float * 256),
    ]


class CoreTempReader:
    """Читает температуру ядер и частоту CPU из read-only shared memory Core Temp."""

    _MAPPING_NAMES = ("Global\\CoreTempMappingObjectEx", "CoreTempMappingObjectEx")
    _MUTEX_NAME = "CoreTempMutexObject"
    _FILE_MAP_READ = 0x0004
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_ABANDONED = 0x00000080
    _WAIT_TIMEOUT = 0x00000102
    _WAIT_FAILED = 0xFFFFFFFF

    def read_temperature(self) -> float | None:
        """Возвращает максимальную температуру ядер в °C или None, если Core Temp не запущен."""
        return self.read_metrics().temperature_c

    def read_metrics(self) -> CoreTempMetrics:
        """Один проход по shared memory: (температура ядер, частота CPU) или Nones.

        Обе метрики читаются из одного отображения — второй заход за мьютексом
        не нужен. Если Core Temp не запущен, возвращает ``(None, None)``.
        """
        if os.name != "nt":
            return CoreTempMetrics(None, None)

        kernel32 = self._kernel32()
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.ReleaseMutex.restype = ctypes.c_int
        kernel32.OpenFileMappingW.argtypes = (
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_wchar_p,
        )
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_size_t,
        )
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        kernel32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        kernel32.UnmapViewOfFile.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int

        mutex = kernel32.CreateMutexW(None, False, self._MUTEX_NAME)
        if not mutex:
            raise self._windows_error("не удалось открыть mutex Core Temp")

        owns_mutex = False
        mapping = None
        view = None
        try:
            wait = kernel32.WaitForSingleObject(mutex, 250)
            if wait in (self._WAIT_OBJECT_0, self._WAIT_ABANDONED):
                owns_mutex = True
            elif wait == self._WAIT_TIMEOUT:
                raise CpuReadError("тайм-аут ожидания данных Core Temp")
            elif wait == self._WAIT_FAILED:
                raise self._windows_error("ошибка ожидания mutex Core Temp")
            else:
                raise CpuReadError(f"неожиданный результат ожидания Core Temp: 0x{wait:08x}")

            global_access_denied = False
            for name in self._MAPPING_NAMES:
                mapping = kernel32.OpenFileMappingW(self._FILE_MAP_READ, False, name)
                if not mapping:
                    error = ctypes.get_last_error()
                    if error == 2:
                        continue
                    if name.startswith("Global\\") and error == 5:
                        global_access_denied = True
                        continue
                    raise self._windows_error("не удалось открыть shared memory Core Temp")
                view = kernel32.MapViewOfFile(
                    mapping,
                    self._FILE_MAP_READ,
                    0,
                    0,
                    ctypes.sizeof(_CoreTempSharedDataEx),
                )
                if not view:
                    raise self._windows_error("не удалось отобразить shared memory Core Temp")
                data = ctypes.cast(view, ctypes.POINTER(_CoreTempSharedDataEx)).contents
                return self._metrics_from_data(data)
            if global_access_denied:
                raise CpuReadError("доступ к глобальной shared memory Core Temp запрещён")
            return CoreTempMetrics(None, None)
        finally:
            if view:
                kernel32.UnmapViewOfFile(view)
            if mapping:
                kernel32.CloseHandle(mapping)
            if owns_mutex:
                kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)

    @staticmethod
    def _kernel32():
        return ctypes.WinDLL("kernel32", use_last_error=True)

    @staticmethod
    def _windows_error(context: str) -> CpuReadError:
        error = ctypes.get_last_error()
        detail = ctypes.FormatError(error).strip() if error else "неизвестная ошибка Windows"
        return CpuReadError(f"{context}: {detail}")

    @staticmethod
    def _metrics_from_data(data: _CoreTempSharedDataEx) -> CoreTempMetrics:
        """Валидирует shared memory и собирает (температуру, частоту) из одного копирования."""
        if data.uiStructVersion != 2:
            raise CpuReadError(
                f"неподдерживаемая версия shared memory Core Temp: {data.uiStructVersion}"
            )
        core_count = int(data.uiCoreCnt)
        cpu_count = int(data.uiCPUCnt)
        if not 0 < core_count <= len(data.fTemp) or not 0 < cpu_count <= len(data.uiTjMax):
            raise CpuReadError(
                f"некорректные счётчики Core Temp: ядер={core_count}, процессоров={cpu_count}"
            )
        total_cores = core_count * cpu_count
        if total_cores > len(data.fTemp):
            raise CpuReadError(f"некорректное число логических ядер Core Temp: {total_cores}")

        temperatures: list[float] = []
        for index, raw in enumerate(data.fTemp[:total_cores]):
            value = float(raw)
            if not math.isfinite(value):
                continue
            if data.ucDeltaToTjMax:
                cpu_index = index // core_count
                value = float(data.uiTjMax[cpu_index]) - value
            if data.ucFahrenheit:
                value = (value - 32.0) * 5.0 / 9.0
            if 0.0 <= value <= 150.0:
                temperatures.append(value)

        # fCPUSpeed — фактическая частота (базовая × множитель), МГц; 0/мусор → нет данных
        speed = float(data.fCPUSpeed)
        frequency = speed if math.isfinite(speed) and speed > 0.0 else None
        return CoreTempMetrics(max(temperatures) if temperatures else None, frequency)


__all__ = [
    "CoreTempMetrics",
    "CoreTempReader",
    "CpuReadError",
    "CpuSampler",
    "CpuSnapshot",
]
