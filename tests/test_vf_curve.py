"""Тесты валидации кривой V/F (tuner.validate_vf_curve) — без железа.

Проверяют, что до вызова IGCL кривая проходит форму/границы:
границы (не перегруз/не провал), монотонность, минимум точек.
"""

from __future__ import annotations

import pytest

from arctuner.tuner import (
    VF_MAX_FREQ_MHZ,
    VF_MAX_VOLTAGE_MV,
    SafetyLimitError,
    VfPoint,
    validate_vf_curve,
)


def _curve(*pairs: tuple[float, float]) -> list[VfPoint]:
    return [VfPoint(v, f) for v, f in pairs]


def test_accepts_valid_curve() -> None:
    validate_vf_curve(_curve((745, 770), (945, 2610), (1195, 3170)))


def test_rejects_single_point() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((800, 2000)))


def test_rejects_empty() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve([])


def test_rejects_overvoltage() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((VF_MAX_VOLTAGE_MV + 1, 1000), (1300, 2000)))


def test_rejects_underfrequency_floor() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((800, 100), (900, 2000)))


def test_rejects_overfrequency() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((800, VF_MAX_FREQ_MHZ + 1), (900, VF_MAX_FREQ_MHZ + 2)))


def test_rejects_decreasing_frequency() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((800, 2000), (900, 1500)))


def test_rejects_decreasing_voltage() -> None:
    with pytest.raises(SafetyLimitError):
        validate_vf_curve(_curve((900, 1500), (800, 2000)))


def test_allows_equal_voltage_plateau() -> None:
    """Плоский участок (одно напряжение) допустим — убывания нет."""
    validate_vf_curve(_curve((800, 2000), (800, 2400), (900, 2600)))


def test_allows_equal_frequency_tail() -> None:
    """Хвост с одинаковой частотой (как стоковая 3170/3170) допустим."""
    validate_vf_curve(_curve((1095, 3050), (1145, 3170), (1195, 3170)))
