"""C-структуры (_ctl_*_t) и enum'ы IGCL на ctypes.

Имена полей сохраняются как в igcl_api.h — чтобы сверка с первоисточником была
тривиальной. Изменения здесь требуют теста layout (см. tests/).

ЗАМЕЧАНИЕ: точные размеры/смещения структур нужно сверить с igcl_api.h для вашей
версии драйвера. Значения ниже — скелет, который уточняется под реальный header.
"""

from __future__ import annotations

import ctypes
from enum import IntEnum


class CtlResult(ctypes.c_uint32):
    """_ctl_result_t. 0 = CTL_RESULT_SUCCESS."""


CTL_RESULT_SUCCESS = 0


class OcTelemetryUnit(IntEnum):
    """_ctl_units_t — единицы значений телеметрии."""

    FREQUENCY_MHZ = 0
    VOLTAGE_VOLTS = 1
    POWER_WATTS = 2
    TEMPERATURE_CELSIUS = 3
    MEMORY_GBPS = 4
    POWER_MILLI_WATTS = 5
    VOLTAGE_MILLIVOLTS = 6
    MEMORY_BYTES = 7


class OcItemType(IntEnum):
    """Известные пункты настройки (GetAllOverclockingItems).

    Значения — как в перечислении IGCC-слоя; уточнить под igcl_api.h.
    """

    GPU_FREQUENCY_OFFSET = 0
    GPU_VOLTAGE_OFFSET = 1
    POWER_LIMIT = 2
    TEMPERATURE_LIMIT = 3
    VRAM_FREQUENCY_OFFSET = 4
    VRAM_MEM_SPEED_LIMIT = 5
    VRAM_VOLTAGE_OFFSET = 6


# --- Структуры. Скелет: уточнить layout под igcl_api.h вашей версии драйвера. ---


class CtlVoltageFrequencyPoint(ctypes.Structure):
    """_ctl_voltage_frequency_point_t — точка кривой V/F."""

    _fields_ = [
        ("voltage", ctypes.c_uint32),  # VoltageMillivolts (сверить!)
        ("frequency", ctypes.c_uint32),  # FrequencyMHz
    ]


class CtlOcControlItem(ctypes.Structure):
    """_ctl_oc_control_info_t — описание одного регулируемого пункта.

    Скелет. Реальные поля (min/max/step/default/isSupported/isReadOnly/canControl/unit)
    сверить с igcl_api.h.
    """

    _fields_ = [
        ("itemType", ctypes.c_uint32),
        ("min", ctypes.c_double),
        ("max", ctypes.c_double),
        ("step", ctypes.c_double),
        ("default", ctypes.c_double),
        ("isSupported", ctypes.c_bool),
        ("isReadOnly", ctypes.c_bool),
        ("canControl", ctypes.c_bool),
        ("unit", ctypes.c_uint32),
    ]


class CtlPowerTelemetry(ctypes.Structure):
    """_ctl_power_telemetry_t — снимок телеметрии питания.

    Скелет: перечень и порядок полей должны совпадать с igcl_api.h.
    Включает флаги ограничения (GpuPowerLimited и т.п.) и датчики.
    """

    _fields_ = [
        ("version", ctypes.c_uint32),
        # Флаги ограничения (структура-обёртка уточняется по header).
        ("gpuPowerLimited", ctypes.c_bool),
        ("gpuTemperatureLimited", ctypes.c_bool),
        ("gpuCurrentLimited", ctypes.c_bool),
        ("gpuVoltageLimited", ctypes.c_bool),
        ("gpuUtilizationLimited", ctypes.c_bool),
        # Датчики (значение + единица) — уточнить точный layout.
        ("gpuVoltage", ctypes.c_double),
        ("gpuCurrentClockFrequency", ctypes.c_double),
        ("gpuCurrentTemperature", ctypes.c_double),
        ("vramVoltage", ctypes.c_double),
        ("vramCurrentEffectiveFrequency", ctypes.c_double),
        ("vramCurrentTemperature", ctypes.c_double),
        ("totalCardEnergyCounter", ctypes.c_double),
    ]
