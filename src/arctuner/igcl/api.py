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

    def initialize(self) -> None:
        """ctlInit — инициализация API. Требует USE_LEVEL_ZERO для телеметрии."""
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
        """ctlClose — освобождение handle API."""
        if self._api is None:
            return
        _check(self.loader.dll.ctlClose(CtlApiHandle(self._api)), "ctlClose")
        self._api = None

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
        )

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
        }

    # Контекстный менеджер для автоматического shutdown
    def __enter__(self) -> IgclApi:
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
