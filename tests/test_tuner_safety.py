"""Тесты safety-логики tuner.py (без железа).

Проверяют, что ЖЁСТКИЕ инварианты проекта и проверки IGCL-диапазонов
реально блокируют недопустимые значения. Это ядро безопасности записи.
"""

from __future__ import annotations

import pytest

from arctuner.igcl.api import OcControlInfo
from arctuner.tuner import (
    MAX_POWER_LIMIT_PERCENT,
    MAX_VRAM_SPEED_GBPS,
    SafetyLimitError,
    _check_hard_limit,
    _check_igcl_range,
)


def _info(lo: float, hi: float, step: float = 1.0, supported: bool = True) -> OcControlInfo:
    return OcControlInfo(
        supported=supported, relative=False, units=4, min=lo, max=hi, step=step, default=lo
    )


# --- Уровень 1: жёсткие инварианты проекта ---


def test_hard_limit_rejects_above_120_power() -> None:
    """Лимит питания >120% (аппаратный потолок) — всегда блок."""
    with pytest.raises(SafetyLimitError):
        _check_hard_limit("power_limit", MAX_POWER_LIMIT_PERCENT + 1, 50.0, 120.0)
    with pytest.raises(SafetyLimitError):
        _check_hard_limit("power_limit", 226.0, 50.0, 120.0)


def test_hard_limit_accepts_boundary_120() -> None:
    """120% — ровно потолок, допустим."""
    _check_hard_limit("power_limit", MAX_POWER_LIMIT_PERCENT, 50.0, 120.0)


def test_hard_limit_rejects_vram_above_nominal_without_force() -> None:
    """Память >20 (номинал SC20) блокируется без явного обхода потолка."""
    with pytest.raises(SafetyLimitError):
        _check_hard_limit("vram_speed", MAX_VRAM_SPEED_GBPS + 0.5, 19.0, MAX_VRAM_SPEED_GBPS)
    # 22 допустимо, если потолок поднят (allow_beyond_nominal → hi=22).
    _check_hard_limit("vram_speed", 21.0, 19.0, 22.0)


# --- Уровень 2: диапазон/supported из IGCL ---


def test_igcl_range_rejects_out_of_range() -> None:
    with pytest.raises(SafetyLimitError):
        _check_igcl_range("x", 101.0, _info(50.0, 100.0))
    with pytest.raises(SafetyLimitError):
        _check_igcl_range("x", 49.0, _info(50.0, 100.0))


def test_igcl_range_rejects_unsupported() -> None:
    with pytest.raises(SafetyLimitError):
        _check_igcl_range("x", 60.0, _info(50.0, 100.0, supported=False))


def test_igcl_range_rejects_non_multiple_of_step() -> None:
    """150 не кратен шагу 1 от min 50? — 50+100*1=150 кратно, возьмём дробный шаг."""
    with pytest.raises(SafetyLimitError):
        _check_igcl_range("x", 50.5, _info(50.0, 100.0, step=1.0))


def test_igcl_range_accepts_exact_multiple() -> None:
    _check_igcl_range("x", 55.0, _info(50.0, 100.0, step=1.0))


def test_igcl_range_accepts_inclusive_bounds() -> None:
    _check_igcl_range("x", 50.0, _info(50.0, 100.0, step=1.0))
    _check_igcl_range("x", 100.0, _info(50.0, 100.0, step=1.0))
