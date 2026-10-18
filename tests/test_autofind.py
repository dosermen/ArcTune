"""Тесты логики autofind БЕЗ железа (фейковый API).

Главная проверка — ГАРАНТИЯ ОТКАТА: при срабатывании гарда/ошибке карта
возвращается к последнему стабильному значению, а не остаётся на провалившемся.
Стресс тут не нужен — сценарии задаются через фейковую телеметрию.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

from arctuner.autofind import (
    MODE_UNDERVOLT,
    AutofindConfig,
    autofind,
)
from arctuner.igcl.api import OcControlInfo, TelemetrySnapshot


class _FakeDll:
    """Имитация loader.dll: Get-функции пишут заданное значение, всегда 0."""

    def __init__(self, values: dict[str, float]) -> None:
        self._values = values

    def __getattr__(self, name: str):
        def fn(handle, p_out=None, *a):
            if name in self._values and p_out is not None:
                with contextlib.suppress(AttributeError):
                    p_out._obj.value = self._values[name]  # byref(c_double) -> _obj
            return 0

        return fn


def _oc_info(lo: float = 0.0, hi: float = 100.0, step: float = 1.0) -> OcControlInfo:
    return OcControlInfo(
        supported=True, relative=False, units=3, min=lo, max=hi, step=step, default=lo
    )


def _make_api(baseline: float, temp_at_or_below: tuple[float, float]) -> MagicMock:
    """Создаёт фейковый API.

    temp_at_or_below: (порог_значения,高温) — если применённое значение ≤ порога,
    телеметрия «перегрелась» (гард сработает), иначе нормальная температура.
    """
    api = MagicMock()
    api.loader.has.return_value = True
    api.loader.dll = _FakeDll(
        {
            "ctlOverclockGpuMaxVoltageOffsetGetV2": baseline,
            "ctlOverclockGpuFrequencyOffsetGetV2": 0.0,
            "ctlOverclockPowerLimitGetV2": 100.0,
            "ctlOverclockTemperatureLimitGetV2": 100.0,
            "ctlOverclockVramMemSpeedLimitGetV2": 19.0,
        }
    )
    api.read_oc_properties.return_value = {
        "supported": True,
        "gpuVoltageOffset": _oc_info(),
        "gpuFrequencyOffset": _oc_info(-300.0, 1000.0),
    }

    state = {"applied": baseline, "reads": 0}
    thr, hot_temp = temp_at_or_below

    def snap(_handle):
        state["reads"] += 1
        hot = state["applied"] <= thr
        return TelemetrySnapshot(
            gpu_voltage_v=1.0,
            gpu_freq_mhz=2600.0,
            gpu_temp_c=hot_temp if hot else 55.0,
            gpu_power_percent=60.0,
            vram_voltage_v=1.35,
            vram_eff_freq_mbps=19.75,
            vram_temp_c=50.0,
            power_limited=hot,
            temperature_limited=False,
            current_limited=False,
            voltage_limited=False,
            utilization_limited=False,
            # монотонно растёт -> нагрузка всегда «обнаружена»
            activity_counter=state["reads"],
        )

    api.read_power_telemetry.side_effect = snap

    def record_set(_handle, value):
        state["applied"] = value

    api.set_gpu_voltage_offset.side_effect = record_set
    api.state = state  # для проверок в тестах
    return api


def test_undervolt_rollback_on_temp_guard() -> None:
    """Гард по температуре вызывает откат к последнему стабильному значению."""
    # baseline 50, шаг 1 вниз: 49, 48 -> ок; 47 -> «перегрев» -> откат на 48
    api = _make_api(baseline=50.0, temp_at_or_below=(47.0, 95.0))
    cfg = AutofindConfig(
        mode=MODE_UNDERVOLT, step=1.0, max_steps=20, settle_seconds=0, load_timeout_seconds=0
    )
    res = autofind(api, handle=1, cfg=cfg)

    assert res.baseline == 50.0
    assert res.best_stable == 48.0  # 49 и 48 прошли, 47 откатился
    assert res.steps_taken == 2
    assert "откат" in res.stopped_reason
    # карта реально откатена к 48 (последний стабильный), а не 47
    assert api.state["applied"] == 48.0
    assert api.set_gpu_voltage_offset.call_args.args[1] == 48.0


def test_no_guard_runs_to_step_limit() -> None:
    """Без срабатываний поиск идёт до max_steps и заканчивается штатно."""
    # порог очень низкий — гард никогда не сработает
    api = _make_api(baseline=50.0, temp_at_or_below=(-999.0, 95.0))
    cfg = AutofindConfig(
        mode=MODE_UNDERVOLT, step=1.0, max_steps=5, settle_seconds=0, load_timeout_seconds=0
    )
    res = autofind(api, handle=1, cfg=cfg)

    assert res.steps_taken == 5
    assert res.best_stable == 45.0  # 50 - 1*5
    assert res.stopped_reason == "достигнут лимит шагов"
    assert api.state["applied"] == 45.0


def test_first_step_failing_keeps_baseline() -> None:
    """Если сразу провал — возврат к базовому значению, 0 стабильных шагов."""
    # уже baseline «горячий» -> первый же шаг (49) провалится
    api = _make_api(baseline=50.0, temp_at_or_below=(49.0, 95.0))
    cfg = AutofindConfig(
        mode=MODE_UNDERVOLT, step=1.0, max_steps=10, settle_seconds=0, load_timeout_seconds=0
    )
    res = autofind(api, handle=1, cfg=cfg)

    assert res.steps_taken == 0
    assert res.best_stable == 50.0  # откат к базе
    assert api.state["applied"] == 50.0


def test_history_records_each_step() -> None:
    api = _make_api(baseline=50.0, temp_at_or_below=(47.0, 95.0))
    cfg = AutofindConfig(
        mode=MODE_UNDERVOLT, step=1.0, max_steps=20, settle_seconds=0, load_timeout_seconds=0
    )
    res = autofind(api, handle=1, cfg=cfg)
    # 49(ok), 48(ok), 47(fail) -> 3 записи
    assert len(res.history) == 3
    assert [h["passed"] for h in res.history] == [True, True, False]
    assert res.history[-1]["reason"] is not None
