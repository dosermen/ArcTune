"""Тесты профилей устройств (device_profile + уровень-1 рамок tuner) — без железа.

Ядро универсальности: B580 (0xE20B) сохраняет проверенные потолки (питание
120%, память 19..22), а прочие дискретные Arc доверяют честному min/max/step
из IGCL (уровень-2), без чужих для них B580-границ. Нечитаемый device_id →
строгий B580-фолбэк (security over convenience).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arctuner.device_profile import (
    B580_PROFILE,
    DEVICE_ID_B580,
    GENERIC_PROFILE,
    profile_for_device_id,
)
from arctuner.igcl.api import OcControlInfo
from arctuner.tuner import SafetyLimitError, set_power_limit, set_vram_speed

_HANDLE = object()
_DEVICE_ID_OTHER = 0x56A0  # условная «чужая» Arc (не B580)


def _info(lo: float, hi: float, step: float = 1.0, supported: bool = True) -> OcControlInfo:
    return OcControlInfo(
        supported=supported, relative=False, units=4, min=lo, max=hi, step=step, default=lo
    )


class _StubLoader:
    """has() всегда False → _read_current вернёт None («было» неизвестно)."""

    def has(self, _name: str) -> bool:
        return False


class _FakeApi:
    """API-дублёр: управляемый device_id, диапазоны IGCL, журнал set-вызовов."""

    def __init__(
        self,
        device_id: int | None,
        *,
        power_range: tuple[float, float] = (50.0, 120.0),
        vram_range: tuple[float, float] = (19.0, 22.0),
    ) -> None:
        self._device_id = device_id
        self.calls: list[str] = []
        self.loader = _StubLoader()
        self._ranges = {
            "supported": True,
            "powerLimit": _info(*power_range),
            "vramMemSpeedLimit": _info(vram_range[0], vram_range[1], step=0.001),
        }

    def get_device_properties(self, _handle) -> SimpleNamespace:
        if self._device_id is None:
            raise OSError("device_id недоступен")
        return SimpleNamespace(device_id=self._device_id)

    def read_oc_properties(self, _handle) -> dict:
        return self._ranges

    def set_power_limit(self, _handle, percent: float) -> None:
        self.calls.append(f"power:{percent:g}")

    def set_vram_mem_speed(self, _handle, gbps: float) -> None:
        self.calls.append(f"vram:{gbps:g}")


# --- Разрешение профиля по device_id ---


def test_profile_for_b580_device_id() -> None:
    assert profile_for_device_id(DEVICE_ID_B580) is B580_PROFILE
    assert profile_for_device_id(0xE20B) is B580_PROFILE


def test_profile_for_unknown_device_id_is_generic() -> None:
    assert profile_for_device_id(_DEVICE_ID_OTHER) is GENERIC_PROFILE
    assert profile_for_device_id(0x1234) is GENERIC_PROFILE


def test_generic_profile_has_no_level1_caps() -> None:
    """GENERIC не накладывает уровня-1: все потолки None → доверяем IGCL."""
    p = GENERIC_PROFILE
    assert p.max_power_percent is None
    assert p.min_power_percent is None
    assert p.vram_min_gbps is None
    assert p.vram_nominal_gbps is None
    assert p.vram_max_gbps is None
    assert p.tested is False


def test_b580_profile_matches_verified_caps() -> None:
    p = B580_PROFILE
    assert p.tested is True
    assert p.max_power_percent == 120.0
    assert p.min_power_percent == 50.0
    assert (p.vram_min_gbps, p.vram_nominal_gbps, p.vram_max_gbps) == (19.0, 20.0, 22.0)


def test_status_note_empty_for_b580() -> None:
    """Проверенное устройство (B580) — без предупреждающей пометки."""
    from arctuner.device_profile import profile_status_note

    assert profile_status_note(DEVICE_ID_B580) == ""
    assert profile_status_note(0xE20B) == ""


def test_status_note_warns_for_unknown_device() -> None:
    """Неизвестная карта — честная пометка «не проверено» (инвариант AGENTS.md)."""
    from arctuner.device_profile import profile_status_note

    note = profile_status_note(_DEVICE_ID_OTHER)
    assert note  # непустая
    assert "не проверено" in note


# --- Универсальность: питание ---


def test_b580_power_above_120_rejected_even_if_igcl_allows() -> None:
    """B580: level-1 потолок 120% отклоняет 130, хотя IGCL объявил max=150."""
    api = _FakeApi(DEVICE_ID_B580, power_range=(50.0, 150.0))
    with pytest.raises(SafetyLimitError):
        set_power_limit(api, _HANDLE, 130.0)
    assert api.calls == []


def test_unknown_power_trusts_igcl_max() -> None:
    """Не-B580: level-1 нет → 130 проходит, если IGCL объявил max=150."""
    api = _FakeApi(_DEVICE_ID_OTHER, power_range=(50.0, 150.0))
    report = set_power_limit(api, _HANDLE, 130.0)
    assert report.new == 130.0
    assert api.calls == ["power:130"]


def test_unknown_power_still_capped_by_igcl() -> None:
    """Не-B580: вне IGCL-диапазона — всё равно блок (уровень-2 device-aware)."""
    api = _FakeApi(_DEVICE_ID_OTHER, power_range=(50.0, 150.0))
    with pytest.raises(SafetyLimitError):
        set_power_limit(api, _HANDLE, 160.0)
    assert api.calls == []


def test_unreadable_device_id_falls_back_to_b580() -> None:
    """Нечитаемый device_id → строгий B580: 130 блок, хотя IGCL max=150."""
    api = _FakeApi(None, power_range=(50.0, 150.0))
    with pytest.raises(SafetyLimitError):
        set_power_limit(api, _HANDLE, 130.0)
    assert api.calls == []


# --- Универсальность: память (главный кейс — 16 Гбит/с карта) ---


def test_unknown_vram_allows_below_b580_floor() -> None:
    """Не-B580 с IGCL [8, 16]: 15 Гбит/с проходит (B580-граница 19 отвергла бы)."""
    api = _FakeApi(_DEVICE_ID_OTHER, vram_range=(8.0, 16.0))
    report = set_vram_speed(api, _HANDLE, 15.0)
    assert report.new == 15.0
    assert api.calls == ["vram:15"]


def test_unknown_vram_still_capped_by_igcl() -> None:
    """Не-B580: 17 вне IGCL [8, 16] — блок (уровень-2)."""
    api = _FakeApi(_DEVICE_ID_OTHER, vram_range=(8.0, 16.0))
    with pytest.raises(SafetyLimitError):
        set_vram_speed(api, _HANDLE, 17.0)
    assert api.calls == []


def test_b580_vram_keeps_nominal_gate() -> None:
    """B580: 21 требует allow_beyond_nominal (номинал 20), 24 блок всегда."""
    api = _FakeApi(DEVICE_ID_B580, vram_range=(19.0, 22.0))
    with pytest.raises(SafetyLimitError):
        set_vram_speed(api, _HANDLE, 21.0)  # без флага
    report = set_vram_speed(api, _HANDLE, 21.0, allow_beyond_nominal=True)
    assert report.new == 21.0
    with pytest.raises(SafetyLimitError):
        set_vram_speed(api, _HANDLE, 24.0, allow_beyond_nominal=True)  # вне IGCL max=22
    assert api.calls == ["vram:21"]


def test_b580_vram_below_floor_rejected() -> None:
    """B580: 18 ниже минимума чипа (19) — блок."""
    api = _FakeApi(DEVICE_ID_B580, vram_range=(19.0, 22.0))
    with pytest.raises(SafetyLimitError):
        set_vram_speed(api, _HANDLE, 18.0)
    assert api.calls == []
