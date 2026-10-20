"""Python-обёртки над ctl* функциями IGCL + маппинг ошибок.

Реализация низкоуровневая: точные ctypes-сигнатуры из igcl_api.h, преобразование
результатов в dataclass, не-успешный код → IgclError. Read-only операции безопасны.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from arctuner.igcl import types as T
from arctuner.igcl.loader import IgclLoader


class IgclError(RuntimeError):
    """Ошибка IGCL (не-успешный _ctl_result_t)."""

    def __init__(self, code: int, func: str) -> None:
        self.code = code
        self.func = func
        name = T.CtlResult(code).name if code in T.CtlResult._value2member_map_ else "UNKNOWN"
        super().__init__(f"{func} → {name} (0x{code:08X})")


def _check(code: int, func: str) -> None:
    if code != T.CTL_RESULT_SUCCESS:
        raise IgclError(code, func)


# --- Дескрипторы handle (непрозрачные указатели) ---

CtlApiHandle = ctypes.c_void_p
CtlDeviceHandle = ctypes.c_void_p
CtlFanHandle = ctypes.c_void_p


@dataclass(frozen=True)
class DeviceInfo:
    """Свойства устройства, извлечённые из _ctl_device_adapter_properties_t."""

    name: str
    vendor_id: int
    device_id: int
    subsystem_id: int
    subsystem_vendor_id: int
    is_integrated: bool
    rev_id: int
    frequency_mhz: int
    num_xe_cores: int

    @property
    def is_arc_b580(self) -> bool:
        """B580 = Device ID 0xE20B (дискретная Battlemage)."""
        return self.device_id == 0xE20B


@dataclass(frozen=True)
class ExtendedDeviceInfo:
    """Расширенные свойства устройства: DeviceInfo + driver_version + firmware."""

    info: DeviceInfo
    driver_version: int  # uint64 из CtlDeviceAdapterProperties.driver_version
    firmware_version_major: int  # из firmware_version.major
    firmware_version_minor: int  # из firmware_version.minor

    @classmethod
    def from_properties(
        cls, info: DeviceInfo, props: T.CtlDeviceAdapterProperties
    ) -> ExtendedDeviceInfo:
        """Создаёт ExtendedDeviceInfo из DeviceInfo + raw ctypes-свойств.

        Args:
            info: уже распарсенные DeviceInfo.
            props: raw ctypes CtlDeviceAdapterProperties.

        Returns:
            ExtendedDeviceInfo с driver_version и firmware_version.
        """
        drv = props.driver_version
        fw = props.firmware_version
        return cls(
            info=info,
            driver_version=drv,
            firmware_version_major=fw.major_version,
            firmware_version_minor=fw.minor_version,
        )

    @property
    def driver_version_str(self) -> str:
        return f"{(self.driver_version >> 16) & 0xFFFF}.{self.driver_version & 0xFFFF}"

    @property
    def firmware_version_str(self) -> str:
        return f"{self.firmware_version_major}.{self.firmware_version_minor}"


@dataclass(frozen=True)
class OcControlInfo:
    """Описание одного регулируемого пункта (min/max/step/default)."""

    supported: bool
    relative: bool
    units: int
    min: float
    max: float
    step: float
    default: float


@dataclass(frozen=True)
class PciStateInfo:
    """Показания PCI-линка: скорость, ReBAR, адрес (read-only)."""

    gen: int | None  # -1 у драйвера = неизвестно
    width: int | None
    max_bandwidth_mbps: float | None  # maxSpeed
    rebar_supported: bool
    rebar_enabled: bool
    address: str  # «домен:шина:устройство.функция»


@dataclass(frozen=True)
class FirmwareInfo:
    """Имя/версия базовой прошивки ('unknown' = драйвер не определил)."""

    name: str
    version: str


@dataclass(frozen=True)
class EccStateInfo:
    """Состояние ECC: поддержка и текущее/отложенное состояние."""

    supported: bool
    can_control: bool
    current: str  # «default» / «enabled» / «disabled»
    pending: str


@dataclass(frozen=True)
class FanConfigInfo:
    """Текущий режим вентилятора (ctlFanGetConfig)."""

    mode: str  # «default» / «fixed» / «table» / «unknown(N)»
    fixed_rpm: int | None  # только для fixed-режима


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Снимок телеметрии + причин ограничения."""

    gpu_voltage_v: float | None
    gpu_freq_mhz: float | None
    gpu_temp_c: float | None
    gpu_power_percent: float | None
    vram_voltage_v: float | None
    vram_eff_freq_mbps: float | None
    vram_temp_c: float | None
    power_limited: bool
    temperature_limited: bool
    current_limited: bool
    voltage_limited: bool
    utilization_limited: bool
    # Монотонный счётчик активности GPU — растёт только под нагрузкой.
    # Нужен autofind, чтобы отличить «стабильно под стрессом» от «тихо в покое».
    activity_counter: int | None = None
    # Накопительные счётчики энергии (Дж) — из них слой telemetry считает
    # мощность в ваттах (ΔE/Δt); мгновенной строки «Вт» в структуре нет.
    gpu_energy_counter_j: float | None = None
    card_energy_counter_j: float | None = None
    # Средняя мощность (Вт) за интервал опроса; заполняется в telemetry.enrich
    # (PowerMeter), в api — всегда None (низкий слой не считает сам).
    gpu_power_w: float | None = None
    card_power_w: float | None = None
    # VR-температуры (Version > 0 структуры; на живой B580 supported). Это
    # температуры фаз питания: перегрев VRM — ограничитель, датчики ядра/памяти
    # его не показывают.
    gpu_vr_temp_c: float | None = None
    vram_vr_temp_c: float | None = None
    sa_vr_temp_c: float | None = None
    # Фактическая пропускная способность памяти (units BANDWIDTH_MBPS) —
    # полезна при разгоне памяти: прирост виден сразу, без бенчмарка.
    vram_read_bandwidth_mbps: float | None = None
    vram_write_bandwidth_mbps: float | None = None
    # Надбавка напряжения VRM ядра (%) — полнота использования разрешённого
    # бюджета при ундервольте/разгоне.
    gpu_over_voltage_percent: float | None = None
    # Счётчик энергии памяти (Дж) → ватты памяти считает PowerMeter (ΔE/Δt).
    vram_energy_counter_j: float | None = None
    # Средняя мощность памяти (Вт); заполняется PowerMeter, в api — None.
    vram_power_w: float | None = None

    def limit_summary(self) -> str:
        """Человекочитаемая строка причин ограничения."""
        flags = [
            name
            for name, on in (
                ("Power", self.power_limited),
                ("Temp", self.temperature_limited),
                ("Current", self.current_limited),
                ("Voltage", self.voltage_limited),
                ("Utilization", self.utilization_limited),
            )
            if on
        ]
        return ", ".join(flags) if flags else "нет (запас есть)"


class IgclApi:
    """Точка входа к IGCL. Держит загруженную библиотеку и handle API."""

    def __init__(self, loader: IgclLoader | None = None) -> None:
        self.loader = loader or IgclLoader()
        self._api: int | None = None
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        dll = self.loader.dll
        if self.loader.has("ctlInit"):
            dll.ctlInit.argtypes = [ctypes.POINTER(T.CtlInitArgs), ctypes.POINTER(CtlApiHandle)]
            dll.ctlInit.restype = ctypes.c_uint32
        if self.loader.has("ctlClose"):
            dll.ctlClose.argtypes = [CtlApiHandle]
            dll.ctlClose.restype = ctypes.c_uint32
        if self.loader.has("ctlEnumerateDevices"):
            dll.ctlEnumerateDevices.argtypes = [
                CtlApiHandle,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(CtlDeviceHandle),
            ]
            dll.ctlEnumerateDevices.restype = ctypes.c_uint32
        if self.loader.has("ctlGetDeviceProperties"):
            dll.ctlGetDeviceProperties.argtypes = [
                CtlDeviceHandle,
                ctypes.POINTER(T.CtlDeviceAdapterProperties),
            ]
            dll.ctlGetDeviceProperties.restype = ctypes.c_uint32
        if self.loader.has("ctlPowerTelemetryGet"):
            dll.ctlPowerTelemetryGet.argtypes = [
                CtlDeviceHandle,
                ctypes.POINTER(T.CtlPowerTelemetry),
            ]
            dll.ctlPowerTelemetryGet.restype = ctypes.c_uint32
        if self.loader.has("ctlOverclockGetProperties"):
            dll.ctlOverclockGetProperties.argtypes = [
                CtlDeviceHandle,
                ctypes.POINTER(T.CtlOcProperties),
            ]
            dll.ctlOverclockGetProperties.restype = ctypes.c_uint32

        # --- Информация об устройстве: PCIe/прошивка/ECC ---
        # Без argtypes ctypes конвертировал handle (Python int, адрес > 32 бит)
        # в C int → OverflowError; `arctune info` падал на первом же вызове.
        for name, ptype in (
            ("ctlPciGetProperties", T.CtlPciProperties),
            ("ctlPciGetState", T.CtlPciState),
            ("ctlGetFirmwareProperties", T.CtlFirmwareProperties),
            ("ctlEccGetProperties", T.CtlEccProperties),
            ("ctlEccGetState", T.CtlEccStateDesc),
        ):
            if self.loader.has(name):
                getattr(dll, name).argtypes = [CtlDeviceHandle, ctypes.POINTER(ptype)]
                getattr(dll, name).restype = ctypes.c_uint32

        # --- Запись разгона: все set-функции принимают (handle, double) ---
        for name in (
            "ctlOverclockGpuFrequencyOffsetSetV2",
            "ctlOverclockGpuMaxVoltageOffsetSetV2",
            "ctlOverclockPowerLimitSetV2",
            "ctlOverclockTemperatureLimitSetV2",
            "ctlOverclockVramMemSpeedLimitSetV2",
        ):
            if self.loader.has(name):
                getattr(dll, name).argtypes = [CtlDeviceHandle, ctypes.c_double]
                getattr(dll, name).restype = ctypes.c_uint32

        # Сброс к дефолту / waiver: (handle)
        for name in ("ctlOverclockResetToDefault", "ctlOverclockWaiverSet"):
            if self.loader.has(name):
                getattr(dll, name).argtypes = [CtlDeviceHandle]
                getattr(dll, name).restype = ctypes.c_uint32

        # --- Вентиляторы ---
        if self.loader.has("ctlEnumFans"):
            dll.ctlEnumFans.argtypes = [
                CtlDeviceHandle,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(CtlFanHandle),
            ]
            dll.ctlEnumFans.restype = ctypes.c_uint32
        if self.loader.has("ctlFanGetProperties"):
            dll.ctlFanGetProperties.argtypes = [CtlFanHandle, ctypes.POINTER(T.CtlFanProperties)]
            dll.ctlFanGetProperties.restype = ctypes.c_uint32
        if self.loader.has("ctlFanGetState"):
            dll.ctlFanGetState.argtypes = [
                CtlFanHandle,
                ctypes.c_int32,
                ctypes.POINTER(ctypes.c_int32),
            ]
            dll.ctlFanGetState.restype = ctypes.c_uint32
        if self.loader.has("ctlFanSetFixedSpeedMode"):
            dll.ctlFanSetFixedSpeedMode.argtypes = [CtlFanHandle, ctypes.POINTER(T.CtlFanSpeed)]
            dll.ctlFanSetFixedSpeedMode.restype = ctypes.c_uint32
        if self.loader.has("ctlFanSetSpeedTableMode"):
            dll.ctlFanSetSpeedTableMode.argtypes = [
                CtlFanHandle,
                ctypes.POINTER(T.CtlFanSpeedTable),
            ]
            dll.ctlFanSetSpeedTableMode.restype = ctypes.c_uint32
        if self.loader.has("ctlFanSetDefaultMode"):
            dll.ctlFanSetDefaultMode.argtypes = [CtlFanHandle]
            dll.ctlFanSetDefaultMode.restype = ctypes.c_uint32
        if self.loader.has("ctlFanGetConfig"):
            dll.ctlFanGetConfig.argtypes = [CtlFanHandle, ctypes.POINTER(T.CtlFanConfig)]
            dll.ctlFanGetConfig.restype = ctypes.c_uint32

        # --- Кривая V/F ---
        if self.loader.has("ctlOverclockReadVFCurve"):
            dll.ctlOverclockReadVFCurve.argtypes = [
                CtlDeviceHandle,
                ctypes.c_int32,  # ctl_vf_curve_type_t
                ctypes.c_int32,  # ctl_vf_curve_details_t
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(T.CtlVoltageFrequencyPoint),
            ]
            dll.ctlOverclockReadVFCurve.restype = ctypes.c_uint32
        if self.loader.has("ctlOverclockWriteCustomVFCurve"):
            dll.ctlOverclockWriteCustomVFCurve.argtypes = [
                CtlDeviceHandle,
                ctypes.c_uint32,
                ctypes.POINTER(T.CtlVoltageFrequencyPoint),
            ]
            dll.ctlOverclockWriteCustomVFCurve.restype = ctypes.c_uint32

    def initialize(self) -> None:
        """ctlInit — инициализация API. Требует USE_LEVEL_ZERO для телеметрии."""
        # Проверка: функция может отсутствовать в старых драйверах.
        if not self.loader.has("ctlInit"):
            raise IgclError(
                T.CtlResult.ERROR_NOT_SUPPORTED,
                "ctlInit отсутствует в загруженной библиотеке — "
                "обновите драйвер Intel Arc (32.0.101.9xxx+)",
            )
        if not self.loader.has("ctlClose"):
            # Раньше блок инициализации молча пропускался, и следующий вызов
            # падал с невнятным ERROR_NOT_INITIALIZED — честнее сказать сразу.
            raise IgclError(
                T.CtlResult.ERROR_NOT_SUPPORTED,
                "ctlClose отсутствует в загруженной библиотеке — обновите драйвер Intel Arc",
            )
        args = T.CtlInitArgs()
        args.Size = ctypes.sizeof(T.CtlInitArgs)
        args.Version = 0
        args.AppVersion = T.ctl_make_version(T.CTL_IMPL_MAJOR_VERSION, T.CTL_IMPL_MINOR_VERSION)
        args.flags = T.CTL_INIT_FLAG_USE_LEVEL_ZERO
        args.SupportedVersion = 0
        handle = CtlApiHandle()
        _check(self.loader.dll.ctlInit(ctypes.byref(args), ctypes.byref(handle)), "ctlInit")
        self._api = handle.value

    def shutdown(self) -> None:
        """ctlClose — освобождение handle API и выгрузка DLL."""
        if self._api is not None:
            _check(self.loader.dll.ctlClose(CtlApiHandle(self._api)), "ctlClose")
            self._api = None
        # Выгружаем DLL, чтобы её файл можно было перезаписать (тесты / hot-reload).
        self.loader.close()

    def _require_api(self) -> int:
        if self._api is None:
            raise IgclError(T.CtlResult.ERROR_NOT_INITIALIZED, "IgclApi")
        return self._api

    def enumerate_devices(self) -> list[CtlDeviceHandle]:
        """ctlEnumerateDevices — список handle GPU."""
        api = self._require_api()
        count = ctypes.c_uint32(0)
        _check(
            self.loader.dll.ctlEnumerateDevices(api, ctypes.byref(count), None),
            "ctlEnumerateDevices",
        )
        n = count.value
        if n == 0:
            return []
        arr = (CtlDeviceHandle * n)()
        _check(
            self.loader.dll.ctlEnumerateDevices(api, ctypes.byref(count), arr),
            "ctlEnumerateDevices",
        )
        # Индексация массива ctypes возвращает int-адрес (или None) для c_void_p.
        return [h for h in arr if h]

    def get_device_properties(self, handle: CtlDeviceHandle) -> DeviceInfo:
        """ctlGetDeviceProperties — свойства устройства."""
        props = T.CtlDeviceAdapterProperties()
        props.Size = ctypes.sizeof(T.CtlDeviceAdapterProperties)
        props.Version = 2  # >=2, чтобы получить subsys/bdf
        _check(
            self.loader.dll.ctlGetDeviceProperties(handle, ctypes.byref(props)),
            "ctlGetDeviceProperties",
        )
        return DeviceInfo(
            name=props.name.decode("ascii", errors="replace").rstrip("\x00"),
            vendor_id=props.pci_vendor_id,
            device_id=props.pci_device_id,
            subsystem_id=props.pci_subsys_id,
            subsystem_vendor_id=props.pci_subsys_vendor_id,
            is_integrated=bool(
                props.graphics_adapter_properties & T.CtlAdapterPropertiesFlag.INTEGRATED
            ),
            rev_id=props.rev_id,
            frequency_mhz=props.Frequency,
            num_xe_cores=props.num_xe_cores,
        )

    def read_raw_device_properties(
        self,
        handle: CtlDeviceHandle,
    ) -> T.CtlDeviceAdapterProperties:
        """ctlGetDeviceProperties — возвращает сырые ctypes-свойства устройства.

        Возвращает:
            CtlDeviceAdapterProperties с полями driver_version, firmware_version и др.
        """
        props = T.CtlDeviceAdapterProperties()
        props.Size = ctypes.sizeof(T.CtlDeviceAdapterProperties)
        props.Version = 2  # >=2, чтобы получить subsys/bdf
        _check(
            self.loader.dll.ctlGetDeviceProperties(handle, ctypes.byref(props)),
            "ctlGetDeviceProperties",
        )
        return props

    def read_power_telemetry(self, handle: CtlDeviceHandle) -> TelemetrySnapshot:
        """ctlPowerTelemetryGet — снимок датчиков и причин ограничения (read-only)."""
        tel = T.CtlPowerTelemetry()
        tel.Size = ctypes.sizeof(T.CtlPowerTelemetry)
        tel.Version = 1  # >0, чтобы получить gpuPowerPercent и пр.
        _check(
            self.loader.dll.ctlPowerTelemetryGet(handle, ctypes.byref(tel)),
            "ctlPowerTelemetryGet",
        )
        return TelemetrySnapshot(
            gpu_voltage_v=T.read_item_value(tel.gpuVoltage),
            gpu_freq_mhz=T.read_item_value(tel.gpuCurrentClockFrequency),
            gpu_temp_c=T.read_item_value(tel.gpuCurrentTemperature),
            gpu_power_percent=T.read_item_value(tel.gpuPowerPercent),
            vram_voltage_v=T.read_item_value(tel.vramVoltage),
            vram_eff_freq_mbps=T.read_item_value(tel.vramCurrentEffectiveFrequency),
            vram_temp_c=T.read_item_value(tel.vramCurrentTemperature),
            power_limited=tel.gpuPowerLimited,
            temperature_limited=tel.gpuTemperatureLimited,
            current_limited=tel.gpuCurrentLimited,
            voltage_limited=tel.gpuVoltageLimited,
            utilization_limited=tel.gpuUtilizationLimited,
            activity_counter=self._read_counter(tel.globalActivityCounter),
            gpu_energy_counter_j=self._read_energy(tel.gpuEnergyCounter),
            card_energy_counter_j=self._read_energy(tel.totalCardEnergyCounter),
            gpu_vr_temp_c=self._read_unit_value(tel.gpuVrTemp, T.CtlUnits.TEMPERATURE_CELSIUS),
            vram_vr_temp_c=self._read_unit_value(tel.vramVrTemp, T.CtlUnits.TEMPERATURE_CELSIUS),
            sa_vr_temp_c=self._read_unit_value(tel.saVrTemp, T.CtlUnits.TEMPERATURE_CELSIUS),
            vram_read_bandwidth_mbps=self._read_unit_value(
                tel.vramReadBandwidth, T.CtlUnits.BANDWIDTH_MBPS
            ),
            vram_write_bandwidth_mbps=self._read_unit_value(
                tel.vramWriteBandwidth, T.CtlUnits.BANDWIDTH_MBPS
            ),
            gpu_over_voltage_percent=self._read_unit_value(
                tel.gpuOverVoltagePercent, T.CtlUnits.PERCENT
            ),
            vram_energy_counter_j=self._read_energy(tel.vramEnergyCounter),
        )

    @staticmethod
    def _read_unit_value(item: T.CtlOcTelemetryItem, units: int) -> float | None:
        """Читает элемент телеметрии как float только при ожидаемых единицах.

        Проверка units защищает от «сырых» чисел неизвестного масштаба: если
        драйвер отдаёт поле в других единицах (или не поддерживает) — None,
        а не обрезанное/переведённое на глаз значение.
        """
        if not item.bSupported or item.units != units:
            return None
        return T.read_item_value(item)

    @staticmethod
    def _read_energy(item: T.CtlOcTelemetryItem) -> float | None:
        """Читает счётчик энергии как Дж; при иных единицах или без поддержки — None.

        Возвращаем голое накопительное значение: расчёт мощности (ΔE/Δt) делает
        слой telemetry, низкий слой только честно повторяет структуру драйвера.
        """
        return IgclApi._read_unit_value(item, T.CtlUnits.ENERGY_JOULES)

    @staticmethod
    def _read_counter(item: T.CtlOcTelemetryItem) -> int | None:
        """Читает ctl_oc_telemetry_item_t как целое (счётчик) либо None."""
        if not item.bSupported:
            return None
        v = item.value
        if item.type == T.CtlDataType.INT64:
            return int(v.data64)
        if item.type == T.CtlDataType.UINT64:
            return int(v.datau64)
        if item.type == T.CtlDataType.INT32:
            return int(v.data32)
        if item.type == T.CtlDataType.UINT32:
            return int(v.datau32)
        # Для float/double-счётчиков отдаём округлённое.
        f = T.read_item_value(item)
        return int(f) if f is not None else None

    def read_oc_properties(self, handle: CtlDeviceHandle) -> dict[str, OcControlInfo]:
        """ctlOverclockGetProperties — диапазоны пунктов разгона (read-only)."""
        props = T.CtlOcProperties()
        props.Size = ctypes.sizeof(T.CtlOcProperties)
        props.Version = 1  # >0, чтобы получить vramMemSpeedLimit/VF limits
        _check(
            self.loader.dll.ctlOverclockGetProperties(handle, ctypes.byref(props)),
            "ctlOverclockGetProperties",
        )

        def conv(c: T.CtlOcControlItem) -> OcControlInfo:
            return OcControlInfo(
                supported=c.bSupported,
                relative=c.bRelative,
                units=c.units,
                min=c.min,
                max=c.max,
                step=c.step,
                default=c.Default,
            )

        return {
            "supported": props.bSupported,  # type: ignore[dict-item]
            "gpuFrequencyOffset": conv(props.gpuFrequencyOffset),
            "gpuVoltageOffset": conv(props.gpuVoltageOffset),
            "powerLimit": conv(props.powerLimit),
            "temperatureLimit": conv(props.temperatureLimit),
            "vramMemSpeedLimit": conv(props.vramMemSpeedLimit),
            # Границы построения кривой V/F (Version > 0). Шире жёстких рамок
            # проекта в tuner.validate_vf_curve — сознательно: валидатор — это
            # безопасная рамка, границы драйвера — лишь информационная оболочка.
            "gpuVFCurveVoltageLimit": conv(props.gpuVFCurveVoltageLimit),
            "gpuVFCurveFrequencyLimit": conv(props.gpuVFCurveFrequencyLimit),
        }

    # --- Запись (⚠️ меняет состояние карты). Вызывается только из tuner.py
    #     после проверок min/max/step/supported и инвариантов проекта. ---

    def _set_value(self, func_name: str, handle: CtlDeviceHandle, value: float) -> None:
        """Вызывает set-функцию (handle, double) и проверяет результат."""
        _check(getattr(self.loader.dll, func_name)(handle, value), func_name)

    def set_power_limit(self, handle: CtlDeviceHandle, percent: float) -> None:
        """ctlOverclockPowerLimitSetV2 — лимит питания, % (макс 120)."""
        self._set_value("ctlOverclockPowerLimitSetV2", handle, percent)

    def set_gpu_freq_offset(self, handle: CtlDeviceHandle, mhz: float) -> None:
        """ctlOverclockGpuFrequencyOffsetSetV2 — смещение частоты ядра, МГц."""
        self._set_value("ctlOverclockGpuFrequencyOffsetSetV2", handle, mhz)

    def set_gpu_voltage_offset(self, handle: CtlDeviceHandle, percent: float) -> None:
        """ctlOverclockGpuMaxVoltageOffsetSetV2 — смещение напряжения, %."""
        self._set_value("ctlOverclockGpuMaxVoltageOffsetSetV2", handle, percent)

    def set_temperature_limit(self, handle: CtlDeviceHandle, celsius: float) -> None:
        """ctlOverclockTemperatureLimitSetV2 — лимит температуры, °C."""
        self._set_value("ctlOverclockTemperatureLimitSetV2", handle, celsius)

    def set_vram_mem_speed(self, handle: CtlDeviceHandle, gbps: float) -> None:
        """ctlOverclockVramMemSpeedLimitSetV2 — скорость памяти, Гбит/с."""
        self._set_value("ctlOverclockVramMemSpeedLimitSetV2", handle, gbps)

    def reset_to_default(self, handle: CtlDeviceHandle) -> None:
        """ctlOverclockResetToDefault — откат всех OC-настроек к дефолту (план отката)."""
        _check(self.loader.dll.ctlOverclockResetToDefault(handle), "ctlOverclockResetToDefault")

    def set_overclock_waiver(self, handle: CtlDeviceHandle) -> None:
        """ctlOverclockWaiverSet — принятие пользователем риска разгона.

        Intel требует этот вызов ПЕРЕД записью напряжения
        (ctlOverclockGpuMaxVoltageOffsetSetV2), отрицательного смещения частоты
        и записи кривой V/F — иначе драйвер возвращает
        CTL_RESULT_ERROR_CORE_OVERCLOCK_WAIVER_NOT_SET (0x44000008).

        По igcl_api.h приложение обязано хотя бы один раз явно предупредить
        пользователя о снижении срока службы и получить согласие; подтверждение
        записи в CLI/GUI и есть такое согласие. Вызов идемпотентен и сам по себе
        настроек не меняет.
        """
        _check(self.loader.dll.ctlOverclockWaiverSet(handle), "ctlOverclockWaiverSet")

    # --- Вентиляторы ---

    def enum_fans(self, handle: CtlDeviceHandle) -> list[CtlFanHandle]:
        """ctlEnumFans — список handle вентиляторов устройства."""
        count = ctypes.c_uint32(0)
        _check(self.loader.dll.ctlEnumFans(handle, ctypes.byref(count), None), "ctlEnumFans")
        n = count.value
        if n == 0:
            return []
        arr = (CtlFanHandle * n)()
        _check(self.loader.dll.ctlEnumFans(handle, ctypes.byref(count), arr), "ctlEnumFans")
        return [h for h in arr if h]

    def get_fan_properties(self, fan: CtlFanHandle) -> T.CtlFanProperties:
        """ctlFanGetProperties — возможности вентилятора (read-only)."""
        props = T.CtlFanProperties()
        props.Size = ctypes.sizeof(T.CtlFanProperties)
        props.Version = 0
        _check(self.loader.dll.ctlFanGetProperties(fan, ctypes.byref(props)), "ctlFanGetProperties")
        return props

    def get_fan_speed(self, fan: CtlFanHandle, units: int | None = None) -> int:
        """ctlFanGetState — текущая скорость вентилятора (-1 = не измеряется).

        Если units не указан, единица выбирается автоматически из
        supportedUnits свойств (у B580 доступен только RPM).
        """
        if units is None:
            props = self.get_fan_properties(fan)
            units = (
                T.CTL_FAN_SPEED_UNITS_RPM
                if props.supportedUnits & 1
                else (T.CTL_FAN_SPEED_UNITS_PERCENT if props.supportedUnits & 2 else 0)
            )
        speed = ctypes.c_int32(0)
        _check(self.loader.dll.ctlFanGetState(fan, units, ctypes.byref(speed)), "ctlFanGetState")
        return speed.value

    def set_fan_fixed_speed(self, fan: CtlFanHandle, speed: int, units: int | None = None) -> None:
        """ctlFanSetFixedSpeedMode — ⚠️ фиксирует скорость вентилятора.

        Единица по умолчанию выбирается из supportedUnits (у B580 — только RPM).
        """
        if units is None:
            props = self.get_fan_properties(fan)
            units = (
                T.CTL_FAN_SPEED_UNITS_RPM
                if props.supportedUnits & 1
                else (T.CTL_FAN_SPEED_UNITS_PERCENT if props.supportedUnits & 2 else 0)
            )
        s = T.CtlFanSpeed()
        s.Size = ctypes.sizeof(T.CtlFanSpeed)
        s.Version = 0
        s.speed = speed
        s.units = units
        _check(
            self.loader.dll.ctlFanSetFixedSpeedMode(fan, ctypes.byref(s)),
            "ctlFanSetFixedSpeedMode",
        )

    def set_fan_speed_table(self, fan: CtlFanHandle, table: T.CtlFanSpeedTable) -> None:
        """ctlFanSetSpeedTableMode — ⚠️ применяет таблицу temp/speed вентилятора."""
        _check(
            self.loader.dll.ctlFanSetSpeedTableMode(fan, ctypes.byref(table)),
            "ctlFanSetSpeedTableMode",
        )

    def set_fan_default(self, fan: CtlFanHandle) -> None:
        """ctlFanSetDefaultMode — возврат вентилятора к аппаратному режиму."""
        _check(self.loader.dll.ctlFanSetDefaultMode(fan), "ctlFanSetDefaultMode")

    # --- Информация об устройстве (read-only) ---

    @staticmethod
    def _pci_speed_str(speed: T.CtlPciSpeed) -> tuple[int | None, int | None, float | None]:
        """Раскладывает ctl_pci_speed_t: (-1 у драйвера = неизвестно) → None."""
        gen = speed.gen if speed.gen >= 0 else None
        width = speed.width if speed.width >= 0 else None
        bw = float(speed.maxBandwidth) if speed.maxBandwidth >= 0 else None
        return gen, width, bw

    def get_pci_state(self, handle: CtlDeviceHandle) -> PciStateInfo:
        """ctlPciGetProperties + ctlPciGetState — ген/ширина/полоса/ReBAR."""
        props = T.CtlPciProperties()
        props.Size = ctypes.sizeof(T.CtlPciProperties)
        props.Version = 0
        _check(
            self.loader.dll.ctlPciGetProperties(handle, ctypes.byref(props)),
            "ctlPciGetProperties",
        )
        state = T.CtlPciState()
        state.Size = ctypes.sizeof(T.CtlPciState)
        state.Version = 0
        _check(self.loader.dll.ctlPciGetState(handle, ctypes.byref(state)), "ctlPciGetState")
        gen, width, _bw = self._pci_speed_str(state.speed)
        _, _, max_bw = self._pci_speed_str(props.maxSpeed)
        a = props.address
        addr = f"{a.domain:04x}:{a.bus:02x}:{a.device:02x}.{a.function}"
        return PciStateInfo(
            gen=gen,
            width=width,
            max_bandwidth_mbps=max_bw / 1_000_000 if max_bw is not None else None,
            rebar_supported=props.resizable_bar_supported,
            rebar_enabled=props.resizable_bar_enabled,
            address=addr,
        )

    def get_firmware_properties(self, handle: CtlDeviceHandle) -> FirmwareInfo:
        """ctlGetFirmwareProperties — имя и версия базовой прошивки."""
        fw = T.CtlFirmwareProperties()
        fw.Size = ctypes.sizeof(T.CtlFirmwareProperties)
        fw.Version = 0
        _check(
            self.loader.dll.ctlGetFirmwareProperties(handle, ctypes.byref(fw)),
            "ctlGetFirmwareProperties",
        )
        return FirmwareInfo(
            name=fw.name.decode(errors="replace").rstrip("\x00") or "unknown",
            version=fw.version.decode(errors="replace").rstrip("\x00") or "unknown",
        )

    def get_ecc_state(self, handle: CtlDeviceHandle) -> EccStateInfo:
        """ctlEccGetProperties + ctlEccGetState — статус ECC памяти."""
        props = T.CtlEccProperties()
        props.Size = ctypes.sizeof(T.CtlEccProperties)
        props.Version = 0
        _check(
            self.loader.dll.ctlEccGetProperties(handle, ctypes.byref(props)),
            "ctlEccGetProperties",
        )
        state = T.CtlEccStateDesc()
        state.Size = ctypes.sizeof(T.CtlEccStateDesc)
        state.Version = 0
        _check(self.loader.dll.ctlEccGetState(handle, ctypes.byref(state)), "ctlEccGetState")
        # Возврат ctl_ecc_state_desc_t: current/pending enum int32.

        def ecc_name(v: int) -> str:
            """Человекочитаемое имя ECC-состояния."""
            try:
                return T.CtlEccState(v).name.replace("_", "").lower() or "default"
            except ValueError:
                return f"unknown({v})"

        return EccStateInfo(
            supported=props.isSupported,
            can_control=props.canControl,
            current=ecc_name(state.currentEccState),
            pending=ecc_name(state.pendingEccState),
        )

    def get_fan_config(self, fan: CtlFanHandle) -> FanConfigInfo:
        """ctlFanGetConfig — текущий режим вентилятора (default/fixed/table)."""
        cfg = T.CtlFanConfig()
        cfg.Size = ctypes.sizeof(T.CtlFanConfig)
        cfg.Version = 0
        _check(self.loader.dll.ctlFanGetConfig(fan, ctypes.byref(cfg)), "ctlFanGetConfig")
        try:
            mode = T.CtlFanSpeedMode(cfg.mode).name.lower()
        except ValueError:
            mode = f"unknown({cfg.mode})"
        fixed_rpm: int | None = None
        if mode == "fixed":
            fixed_rpm = cfg.speedFixed.speed if cfg.speedFixed.speed >= 0 else None
        return FanConfigInfo(mode=mode, fixed_rpm=fixed_rpm)

    # --- Кривая V/F ---

    def read_vf_curve(
        self,
        handle: CtlDeviceHandle,
        curve_type: int = 0,
        detail: int = 0,
    ) -> list[T.CtlVoltageFrequencyPoint]:
        """ctlOverclockReadVFCurve — чтение кривой V/F (read-only).

        Двухвызовная идемпотентная схема IGCL: сначала count=0 → размер,
        затем читаем точки.
        """
        count = ctypes.c_uint32(0)
        _check(
            self.loader.dll.ctlOverclockReadVFCurve(
                handle, curve_type, detail, ctypes.byref(count), None
            ),
            "ctlOverclockReadVFCurve",
        )
        n = count.value
        if n == 0:
            return []
        arr = (T.CtlVoltageFrequencyPoint * n)()
        _check(
            self.loader.dll.ctlOverclockReadVFCurve(
                handle, curve_type, detail, ctypes.byref(count), arr
            ),
            "ctlOverclockReadVFCurve",
        )
        return list(arr)

    # Контекстный менеджер для автоматического shutdown
    def __enter__(self) -> IgclApi:
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
