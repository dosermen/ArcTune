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


# --- Waiver: обязателен перед напряжением / отрицательной частотой / V/F ---


class _LoaderStub:
    """Минимальный Loader-дублёр: has/dll для V2 Get-функций."""

    def __init__(self, funcs: dict | None = None) -> None:
        from types import SimpleNamespace

        self.dll = SimpleNamespace(**(funcs or {}))

    def has(self, name: str) -> bool:
        return hasattr(self.dll, name)


class _ActualGetter:
    """Поддельная V2 Get-функция: возвращает SUCCESS и пишет value в out."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.argtypes = None
        self.restype = None

    def __call__(self, handle, out) -> int:
        out._obj.value = self.value  # ctypes.byref(c_double)
        return 0  # CTL_RESULT_SUCCESS


class _FakeApi:
    """Дублёр IgclApi: отдаёт фиксированные диапазоны и запоминает порядок вызовов."""

    def __init__(self, **overrides: OcControlInfo) -> None:
        base = {
            "supported": True,
            "gpuFrequencyOffset": _info(-300.0, 1000.0),
            "gpuVoltageOffset": _info(0.0, 100.0),
            "powerLimit": _info(50.0, 120.0),
            "temperatureLimit": _info(60.0, 100.0),
            "vramMemSpeedLimit": _info(19.0, 22.0, step=0.001),
        }
        base.update(overrides)
        self._ranges = base
        self.calls: list[str] = []
        self.loader = _LoaderStub()  # чтение «было» по умолчанию недоступно

    def read_oc_properties(self, handle) -> dict:
        return self._ranges

    def set_overclock_waiver(self, handle) -> None:
        self.calls.append("waiver")

    def set_gpu_voltage_offset(self, handle, percent) -> None:
        self.calls.append("voltage")

    def set_gpu_freq_offset(self, handle, mhz) -> None:
        self.calls.append(f"freq:{mhz:g}")

    def set_power_limit(self, handle, percent) -> None:
        self.calls.append("power")


_HANDLE = object()


def test_voltage_offset_calls_waiver_first() -> None:
    """Без waiver драйвер даёт WAIVER_NOT_SET — вызов должен идти ДО set."""
    from arctuner import tuner

    api = _FakeApi()
    tuner.set_gpu_voltage_offset(api, _HANDLE, 5.0)
    assert api.calls == ["waiver", "voltage"]


def test_negative_freq_offset_calls_waiver() -> None:
    """Отрицательное смещение частоты — waiver по igcl_api.h."""
    from arctuner import tuner

    api = _FakeApi()
    tuner.set_gpu_freq_offset(api, _HANDLE, -100.0)
    assert api.calls == ["waiver", "freq:-100"]


def test_positive_freq_offset_without_waiver() -> None:
    """Положительное смещение waiver не требует — не вызываем без нужды."""
    from arctuner import tuner

    api = _FakeApi()
    tuner.set_gpu_freq_offset(api, _HANDLE, 250.0)
    assert api.calls == ["freq:250"]


def test_power_limit_without_waiver() -> None:
    """Лимит питания waiver не требует (подтверждено живыми записями без него)."""
    from arctuner import tuner

    api = _FakeApi()
    tuner.set_power_limit(api, _HANDLE, 110.0)
    assert api.calls == ["power"]


def test_write_report_old_is_actual_value() -> None:
    """«Было» в WriteReport — фактическое значение с карты, а не заводской default.

    Регрессия: карта была на 115% (выставлено сторонним ПО), ArcTune писал 110%,
    а журнал/откат считали «было = 100» (info.default) — откат возвращал не то.
    """
    from arctuner import tuner

    api = _FakeApi()
    api.loader = _LoaderStub({"ctlOverclockPowerLimitGetV2": _ActualGetter(115.0)})
    report = tuner.set_power_limit(api, _HANDLE, 110.0)
    assert report.old == 115.0  # фактическое, не default=50 из _info
    assert report.new == 110.0


def test_vram_21_requires_beyond_nominal_flag() -> None:
    """21 Гбит/с: без флага — блок (номинал 20), с флагом — проходит и пишется."""
    from arctuner import tuner

    api = _FakeApi()
    with pytest.raises(tuner.SafetyLimitError):
        tuner.set_vram_speed(api, _HANDLE, 21.0)

    class _WithSet(_FakeApi):
        def set_vram_mem_speed(self, handle, gbps) -> None:
            self.calls.append(f"vram:{gbps:g}")

    api2 = _WithSet()
    report = tuner.set_vram_speed(api2, _HANDLE, 21.0, allow_beyond_nominal=True)
    assert report.new == 21.0
    assert api2.calls == ["vram:21"]


def test_vram_24_rejected_even_with_flag() -> None:
    """24 Гбит/с: вне диапазона IGCL (max=22) — блок даже с allow_beyond_nominal."""
    from arctuner import tuner

    class _WithSet(_FakeApi):
        def set_vram_mem_speed(self, handle, gbps) -> None:
            self.calls.append(f"vram:{gbps:g}")

    with pytest.raises(tuner.SafetyLimitError):
        tuner.set_vram_speed(_WithSet(), _HANDLE, 24.0, allow_beyond_nominal=True)
