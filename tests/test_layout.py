"""Тесты layout структур igcl/types.py.

ВАЖНО: размеры/смещения должны совпадать с igcl_api.h версии драйвера.
Эти тесты ловят опечатки в types.py. Критичные структуры (init/oc/telemetry)
проверяются здесь: если смещение уедет — вызов IGCL вернёт мусор или упадёт.
Проверка на живой B580 уже подтвердила корректность раскладок.
"""

from __future__ import annotations

import ctypes
import re
from pathlib import Path

from arctuner.igcl import types as T
from arctuner.igcl.types import (
    CtlApplicationId,
    CtlDataValue,
    CtlInitArgs,
    CtlOcControlItem,
    CtlOcTelemetryItem,
    ctl_make_version,
)

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
# Обращения к членам CtlResult: T.CtlResult.X / CtlResult.X в исходниках.
_CTL_RESULT_REF = re.compile(r"\b(?:T\.)?CtlResult\.([A-Z][A-Z0-9_]*)\b")


def test_ctl_result_references_exist() -> None:
    """Все обращения к CtlResult.* в src/ ссылаются на существующие члены.

    Регрессия: опечатка в имени члена (ERROR_NOT_SUPPORTED вместо
    ERROR_UNSUPPORTED_FEATURE) ловится AttributeError только в рантайме —
    этот тест находит её на CI без железа.
    """
    known = set(T.CtlResult.__members__)
    missing: list[str] = []
    for py in _SRC_ROOT.rglob("*.py"):
        for match in _CTL_RESULT_REF.finditer(py.read_text(encoding="utf-8")):
            if match.group(1) not in known:
                missing.append(f"{py.relative_to(_SRC_ROOT)}: {match.group(0)}")
    assert not missing, "несуществующие члены CtlResult:\n" + "\n".join(missing)


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


def test_fan_config_layout() -> None:
    """_ctl_fan_config_t: Size@0, Version@4, mode@8, speedFixed@12, speedTable@28."""
    from arctuner.igcl.types import CtlFanConfig

    assert CtlFanConfig.Size.offset == 0
    assert CtlFanConfig.Version.offset == 4  # u8 + 3 байта паддинга до mode
    assert CtlFanConfig.mode.offset == 8
    assert CtlFanConfig.speedFixed.offset == 12
    assert CtlFanConfig.speedTable.offset == 28
    # CtlFanSpeedTable (12 + 28×32 = 908) → 28 + 908 = 936.
    assert ctypes.sizeof(CtlFanConfig) == 936


def test_ecc_layout() -> None:
    """_ctl_ecc_properties_t (8 байт) и _ctl_ecc_state_desc_t (16 байт)."""
    from arctuner.igcl.types import CtlEccProperties, CtlEccStateDesc

    assert CtlEccProperties.Size.offset == 0
    assert CtlEccProperties.Version.offset == 4
    assert CtlEccProperties.isSupported.offset == 5
    assert CtlEccProperties.canControl.offset == 6
    assert ctypes.sizeof(CtlEccProperties) == 8

    assert CtlEccStateDesc.Size.offset == 0
    assert CtlEccStateDesc.Version.offset == 4
    # enum int32 выравнивается по 4 → после паддинга @8
    assert CtlEccStateDesc.currentEccState.offset == 8
    assert CtlEccStateDesc.pendingEccState.offset == 12
    assert ctypes.sizeof(CtlEccStateDesc) == 16


def test_pci_layout() -> None:
    """PCI-структуры: скорость (24, align 8), адрес (24), свойства (64), состояние (32)."""
    from arctuner.igcl.types import (
        CtlPciAddress,
        CtlPciProperties,
        CtlPciSpeed,
        CtlPciState,
    )

    # CtlPciSpeed: u32@0, u8@4, gen@8, width@12, i64@16 → 24, выравнивание 8.
    assert CtlPciSpeed.Size.offset == 0
    assert CtlPciSpeed.Version.offset == 4
    assert CtlPciSpeed.gen.offset == 8
    assert CtlPciSpeed.width.offset == 12
    assert CtlPciSpeed.maxBandwidth.offset == 16
    assert ctypes.sizeof(CtlPciSpeed) == 24

    assert CtlPciAddress.Size.offset == 0
    assert CtlPciAddress.domain.offset == 8
    assert CtlPciAddress.bus.offset == 12
    assert CtlPciAddress.function.offset == 20
    assert ctypes.sizeof(CtlPciAddress) == 24

    assert CtlPciProperties.Size.offset == 0
    assert CtlPciProperties.address.offset == 8
    # maxSpeed содержит i64 → выравнивание 8 → offset 32.
    assert CtlPciProperties.maxSpeed.offset == 32
    assert CtlPciProperties.resizable_bar_supported.offset == 56
    assert CtlPciProperties.resizable_bar_enabled.offset == 57
    assert ctypes.sizeof(CtlPciProperties) == 64  # 58 → округление до align 8

    assert CtlPciState.Size.offset == 0
    assert CtlPciState.Version.offset == 4
    assert CtlPciState.speed.offset == 8
    assert ctypes.sizeof(CtlPciState) == 32


def test_firmware_properties_layout() -> None:
    """_ctl_firmware_properties_t: строки с Version@4 → name@5, version@69."""
    from arctuner.igcl.types import CTL_FIRMWARE_RESERVED_SIZE, CtlFirmwareProperties

    assert CTL_FIRMWARE_RESERVED_SIZE == 16
    assert CtlFirmwareProperties.Size.offset == 0
    assert CtlFirmwareProperties.Version.offset == 4
    # char-массив выравнивания 1 → идёт сразу после Version, без паддинга.
    assert CtlFirmwareProperties.name.offset == 5
    assert CtlFirmwareProperties.version.offset == 69  # 5 + 64
    # FirmwareConfig (u32) выравнивание 4 → 69+64=133 округляется до 136.
    assert CtlFirmwareProperties.FirmwareConfig.offset == 136
    assert CtlFirmwareProperties.reserved.offset == 140
    assert ctypes.sizeof(CtlFirmwareProperties) == 156
