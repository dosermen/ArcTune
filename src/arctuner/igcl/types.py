"""C-структуры (_ctl_*_t) и enum'ы IGCL на ctypes.

Раскладки взяты из официального заголовка Intel `igcl_api.h` (v1-r1, публичный).
Имена полей сохраняются как в C — чтобы сверка с первоисточником была тривиальной.
ctypes применяет нативное выравнивание, как MSVC, поэтому порядок полей достаточен.
"""

from __future__ import annotations

import ctypes
from enum import IntEnum

# --- Константы версии и флагов (igcl_api.h) ---

CTL_IMPL_MAJOR_VERSION = 1
CTL_IMPL_MINOR_VERSION = 1


def ctl_make_version(major: int, minor: int) -> int:
    """CTL_MAKE_VERSION: (major << 16) | (minor & 0xFFFF)."""
    return (major << 16) | (minor & 0x0000FFFF)


CTL_RESULT_SUCCESS = 0x00000000

# ctl_init_flag_t
CTL_INIT_FLAG_USE_LEVEL_ZERO = 1 << 0  # нужен для телеметрии/частот
CTL_INIT_FLAG_IGSC_FUL = 1 << 1

CTL_MAX_DEVICE_NAME_LEN = 100
CTL_MAX_RESERVED_SIZE = 108
CTL_PSU_COUNT = 5
CTL_FAN_COUNT = 5


# --- Enum'ы (в C это int) ---


class CtlResult(IntEnum):
    """_ctl_result_t — коды результата (ключевые)."""

    SUCCESS = 0x00000000
    ERROR_NOT_INITIALIZED = 0x40000001
    ERROR_ALREADY_INITIALIZED = 0x40000002
    ERROR_DEVICE_LOST = 0x40000003
    ERROR_INSUFFICIENT_PERMISSIONS = 0x40000006
    ERROR_UNSUPPORTED_VERSION = 0x40000009
    ERROR_UNSUPPORTED_FEATURE = 0x4000000A
    ERROR_INVALID_ARGUMENT = 0x4000000B
    ERROR_INVALID_NULL_POINTER = 0x4000000E
    ERROR_NOT_IMPLEMENTED = 0x40000015
    # Коды, относящиеся к разгону (Core, bit 26)
    ERROR_CORE_OVERCLOCK_NOT_SUPPORTED = 0x44000001
    ERROR_CORE_OVERCLOCK_VOLTAGE_OUTSIDE_RANGE = 0x44000002
    ERROR_CORE_OVERCLOCK_FREQUENCY_OUTSIDE_RANGE = 0x44000003
    ERROR_CORE_OVERCLOCK_POWER_OUTSIDE_RANGE = 0x44000004
    ERROR_CORE_OVERCLOCK_TEMPERATURE_OUTSIDE_RANGE = 0x44000005
    ERROR_CORE_OVERCLOCK_RESET_REQUIRED = 0x44000007
    ERROR_CORE_OVERCLOCK_WAIVER_NOT_SET = 0x44000008
    ERROR_CORE_OVERCLOCK_VRAM_MEMORY_SPEED_OUTSIDE_RANGE = 0x4400000D


class CtlUnits(IntEnum):
    """_ctl_units_t — единицы значений."""

    FREQUENCY_MHZ = 0
    OPERATIONS_GTS = 1
    OPERATIONS_MTS = 2
    VOLTAGE_VOLTS = 3
    POWER_WATTS = 4
    TEMPERATURE_CELSIUS = 5
    ENERGY_JOULES = 6
    TIME_SECONDS = 7
    MEMORY_BYTES = 8
    ANGULAR_SPEED_RPM = 9
    POWER_MILLIWATTS = 10
    PERCENT = 11
    MEM_SPEED_GBPS = 12
    VOLTAGE_MILLIVOLTS = 13
    BANDWIDTH_MBPS = 14


class CtlDataType(IntEnum):
    """_ctl_data_type_t — тип данных значения телеметрии."""

    INT8 = 0
    UINT8 = 1
    INT16 = 2
    UINT16 = 3
    INT32 = 4
    UINT32 = 5
    INT64 = 6
    UINT64 = 7
    FLOAT = 8
    DOUBLE = 9


class CtlDeviceType(IntEnum):
    GRAPHICS = 1
    SYSTEM = 2


class CtlAdapterPropertiesFlag(IntEnum):
    INTEGRATED = 1 << 0
    LDA_PRIMARY = 1 << 1
    LDA_SECONDARY = 1 << 2


# --- Union'ы ---


class CtlDataValue(ctypes.Union):
    """_ctl_data_value_t — универсальное значение телеметрии."""

    _fields_ = [
        ("data8", ctypes.c_int8),
        ("datau8", ctypes.c_uint8),
        ("data16", ctypes.c_int16),
        ("datau16", ctypes.c_uint16),
        ("data32", ctypes.c_int32),
        ("datau32", ctypes.c_uint32),
        ("data64", ctypes.c_int64),
        ("datau64", ctypes.c_uint64),
        ("datafloat", ctypes.c_float),
        ("datadouble", ctypes.c_double),
    ]


# --- Простые структуры ---


class CtlApplicationId(ctypes.Structure):
    """_ctl_application_id_t."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


class CtlFirmwareVersion(ctypes.Structure):
    """_ctl_firmware_version_t."""

    _fields_ = [
        ("major_version", ctypes.c_uint64),
        ("minor_version", ctypes.c_uint64),
        ("build_number", ctypes.c_uint64),
    ]


class CtlAdapterBdf(ctypes.Structure):
    """_ctl_adapter_bdf_t."""

    _fields_ = [
        ("bus", ctypes.c_uint8),
        ("device", ctypes.c_uint8),
        ("function", ctypes.c_uint8),
    ]


class CtlInitArgs(ctypes.Structure):
    """_ctl_init_args_t — аргументы ctlInit."""

    _fields_ = [
        ("Size", ctypes.c_uint32),
        ("Version", ctypes.c_uint8),
        ("AppVersion", ctypes.c_uint32),  # ctl_version_info_t = uint32
        ("flags", ctypes.c_uint32),  # ctl_init_flags_t = uint32
        ("SupportedVersion", ctypes.c_uint32),
        ("ApplicationUID", CtlApplicationId),
    ]


class CtlDeviceAdapterProperties(ctypes.Structure):
    """_ctl_device_adapter_properties_t — свойства устройства."""

    _fields_ = [
        ("Size", ctypes.c_uint32),
        ("Version", ctypes.c_uint8),
        ("pDeviceID", ctypes.c_void_p),
        ("device_id_size", ctypes.c_uint32),
        ("device_type", ctypes.c_uint32),
        ("supported_subfunction_flags", ctypes.c_uint32),
        ("driver_version", ctypes.c_uint64),
        ("firmware_version", CtlFirmwareVersion),
        ("pci_vendor_id", ctypes.c_uint32),
        ("pci_device_id", ctypes.c_uint32),
        ("rev_id", ctypes.c_uint32),
        ("num_eus_per_sub_slice", ctypes.c_uint32),
        ("num_sub_slices_per_slice", ctypes.c_uint32),
        ("num_slices", ctypes.c_uint32),
        ("name", ctypes.c_char * CTL_MAX_DEVICE_NAME_LEN),
        ("graphics_adapter_properties", ctypes.c_uint32),
        ("Frequency", ctypes.c_uint32),
        ("pci_subsys_id", ctypes.c_uint16),
        ("pci_subsys_vendor_id", ctypes.c_uint16),
        ("adapter_bdf", CtlAdapterBdf),
        ("num_xe_cores", ctypes.c_uint32),
        ("reserved", ctypes.c_char * CTL_MAX_RESERVED_SIZE),
    ]


# --- О блоке разгона и телеметрии ---


class CtlOcControlItem(ctypes.Structure):
    """_ctl_oc_control_info_t — описание одного регулируемого пункта."""

    _fields_ = [
        ("bSupported", ctypes.c_bool),
        ("bRelative", ctypes.c_bool),
        ("bReference", ctypes.c_bool),
        ("units", ctypes.c_int32),  # ctl_units_t
        ("min", ctypes.c_double),
        ("max", ctypes.c_double),
        ("step", ctypes.c_double),
        ("Default", ctypes.c_double),
        ("reference", ctypes.c_double),
    ]


class CtlOcProperties(ctypes.Structure):
    """_ctl_oc_properties_t — возможности разгона устройства (read-only)."""

    _fields_ = [
        ("Size", ctypes.c_uint32),
        ("Version", ctypes.c_uint8),
        ("bSupported", ctypes.c_bool),
        ("gpuFrequencyOffset", CtlOcControlItem),
        ("gpuVoltageOffset", CtlOcControlItem),
        ("vramFrequencyOffset", CtlOcControlItem),  # deprecated
        ("vramVoltageOffset", CtlOcControlItem),  # deprecated
        ("powerLimit", CtlOcControlItem),
        ("temperatureLimit", CtlOcControlItem),
        ("vramMemSpeedLimit", CtlOcControlItem),  # Version > 0
        ("gpuVFCurveVoltageLimit", CtlOcControlItem),  # Version > 0
        ("gpuVFCurveFrequencyLimit", CtlOcControlItem),  # Version > 0
    ]


class CtlOcTelemetryItem(ctypes.Structure):
    """_ctl_oc_telemetry_item_t — элемент телеметрии (значение + единица)."""

    _fields_ = [
        ("bSupported", ctypes.c_bool),
        ("units", ctypes.c_int32),  # ctl_units_t
        ("type", ctypes.c_int32),  # ctl_data_type_t
        ("value", CtlDataValue),
    ]


class CtlPsuInfo(ctypes.Structure):
    """_ctl_psu_info_t — информация об источнике питания."""

    _fields_ = [
        ("bSupported", ctypes.c_bool),
        ("psuType", ctypes.c_int32),
        ("energyCounter", CtlOcTelemetryItem),
        ("voltage", CtlOcTelemetryItem),
    ]


class CtlPowerTelemetry(ctypes.Structure):
    """_ctl_power_telemetry_t — снимок телеметрии питания (read-only)."""

    _fields_ = [
        ("Size", ctypes.c_uint32),
        ("Version", ctypes.c_uint8),
        ("timeStamp", CtlOcTelemetryItem),
        ("gpuEnergyCounter", CtlOcTelemetryItem),
        ("gpuVoltage", CtlOcTelemetryItem),
        ("gpuCurrentClockFrequency", CtlOcTelemetryItem),
        ("gpuCurrentTemperature", CtlOcTelemetryItem),
        ("globalActivityCounter", CtlOcTelemetryItem),
        ("renderComputeActivityCounter", CtlOcTelemetryItem),
        ("mediaActivityCounter", CtlOcTelemetryItem),
        # Причины ограничения (мгновенные флаги)
        ("gpuPowerLimited", ctypes.c_bool),
        ("gpuTemperatureLimited", ctypes.c_bool),
        ("gpuCurrentLimited", ctypes.c_bool),
        ("gpuVoltageLimited", ctypes.c_bool),
        ("gpuUtilizationLimited", ctypes.c_bool),
        # Память
        ("vramEnergyCounter", CtlOcTelemetryItem),
        ("vramVoltage", CtlOcTelemetryItem),
        ("vramCurrentClockFrequency", CtlOcTelemetryItem),
        ("vramCurrentEffectiveFrequency", CtlOcTelemetryItem),
        ("vramReadBandwidthCounter", CtlOcTelemetryItem),
        ("vramWriteBandwidthCounter", CtlOcTelemetryItem),
        ("vramCurrentTemperature", CtlOcTelemetryItem),
        # Deprecated (всегда false)
        ("vramPowerLimited", ctypes.c_bool),
        ("vramTemperatureLimited", ctypes.c_bool),
        ("vramCurrentLimited", ctypes.c_bool),
        ("vramVoltageLimited", ctypes.c_bool),
        ("vramUtilizationLimited", ctypes.c_bool),
        ("totalCardEnergyCounter", CtlOcTelemetryItem),
        ("psu", CtlPsuInfo * CTL_PSU_COUNT),
        ("fanSpeed", CtlOcTelemetryItem * CTL_FAN_COUNT),
        # Версия > 0
        ("gpuVrTemp", CtlOcTelemetryItem),
        ("vramVrTemp", CtlOcTelemetryItem),
        ("saVrTemp", CtlOcTelemetryItem),
        ("gpuEffectiveClock", CtlOcTelemetryItem),
        ("gpuOverVoltagePercent", CtlOcTelemetryItem),
        ("gpuPowerPercent", CtlOcTelemetryItem),
        ("gpuTemperaturePercent", CtlOcTelemetryItem),
        ("vramReadBandwidth", CtlOcTelemetryItem),
        ("vramWriteBandwidth", CtlOcTelemetryItem),
    ]


def read_item_value(item: CtlOcTelemetryItem) -> float | None:
    """Возвращает значение телеметрии как float по полю type, либо None."""
    if not item.bSupported:
        return None
    t = item.type
    v = item.value
    match t:
        case CtlDataType.INT8:
            return float(v.data8)
        case CtlDataType.UINT8:
            return float(v.datau8)
        case CtlDataType.INT16:
            return float(v.data16)
        case CtlDataType.UINT16:
            return float(v.datau16)
        case CtlDataType.INT32:
            return float(v.data32)
        case CtlDataType.UINT32:
            return float(v.datau32)
        case CtlDataType.INT64:
            return float(v.data64)
        case CtlDataType.UINT64:
            return float(v.datau64)
        case CtlDataType.FLOAT:
            return float(v.datafloat)
        case CtlDataType.DOUBLE:
            return float(v.datadouble)
        case _:
            return None
