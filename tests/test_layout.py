"""Тесты layout структур igcl/types.py.

ВАЖНО: размеры/смещения должны совпадать с igcl_api.h версии драйвера.
Эти тесты ловят опечатки в types.py. Критичные структуры (init/oc/telemetry)
проверяются здесь: если смещение уедет — вызов IGCL вернёт мусор или упадёт.
Проверка на живой B580 уже подтвердила корректность раскладок.
"""

from __future__ import annotations

import ctypes

from arctuner.igcl.types import (
    CtlApplicationId,
    CtlDataValue,
    CtlInitArgs,
    CtlOcControlItem,
    CtlOcTelemetryItem,
    ctl_make_version,
)


def test_ctl_make_version() -> None:
    """CTL_MAKE_VERSION: (major << 16) | (minor & 0xFFFF)."""
    assert ctl_make_version(1, 1) == 0x00010001
    assert ctl_make_version(2, 0) == 0x00020000
    assert ctl_make_version(1, 0xFFFF) == 0x0001FFFF


def test_application_id_size() -> None:
    """_ctl_application_id_t: u32 + u16 + u16 + u8[8] = 16 байт."""
    assert ctypes.sizeof(CtlApplicationId) == 16
    assert CtlApplicationId.Data1.offset == 0
    assert CtlApplicationId.Data2.offset == 4
    assert CtlApplicationId.Data3.offset == 6
    assert CtlApplicationId.Data4.offset == 8


def test_data_value_size() -> None:
    """_ctl_data_value_t — union: самый большой член double/int64 = 8 байт."""
    assert ctypes.sizeof(CtlDataValue) == 8


def test_init_args_layout() -> None:
    """_ctl_init_args_t: Size@0, Version@4, AppVersion@8, flags@12, Supported@16, AppUID@20. Итог 36."""
    assert CtlInitArgs.Size.offset == 0
    assert CtlInitArgs.Version.offset == 4
    # после uint8 Version — 3 байта паддинга, чтобы выровнять AppVersion (u32) по 4
    assert CtlInitArgs.AppVersion.offset == 8
    assert CtlInitArgs.flags.offset == 12
    assert CtlInitArgs.SupportedVersion.offset == 16
    assert CtlInitArgs.ApplicationUID.offset == 20
    assert ctypes.sizeof(CtlInitArgs) == 36


def test_oc_control_info_layout() -> None:
    """_ctl_oc_control_info_t: 3 bool + units(u32) + 5×double. min@8. Итог 48."""
    assert CtlOcControlItem.bSupported.offset == 0
    assert CtlOcControlItem.bRelative.offset == 1
    assert CtlOcControlItem.bReference.offset == 2
    # units (int, align 4) → offset 4; min (double, align 8) → offset 8
    assert CtlOcControlItem.units.offset == 4
    assert CtlOcControlItem.min.offset == 8
    assert CtlOcControlItem.max.offset == 16
    assert CtlOcControlItem.step.offset == 24
    assert CtlOcControlItem.Default.offset == 32
    assert CtlOcControlItem.reference.offset == 40
    assert ctypes.sizeof(CtlOcControlItem) == 48


def test_oc_telemetry_item_layout() -> None:
    """_ctl_oc_telemetry_item_t: bool + units(u32) + type(u32) + value(union 8). value@16. Итог 24."""
    assert CtlOcTelemetryItem.bSupported.offset == 0
    assert CtlOcTelemetryItem.units.offset == 4
    assert CtlOcTelemetryItem.type.offset == 8
    # value — union с double/int64 → выравнивание 8 → offset 16
    assert CtlOcTelemetryItem.value.offset == 16
    assert ctypes.sizeof(CtlOcTelemetryItem) == 24
