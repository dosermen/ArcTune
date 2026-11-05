"""Тесты read-only CPU-сэмплера без привязки к аппаратным датчикам."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("psutil", reason="CPU-телеметрия требует GUI-extra")

from arctuner import cpu


def test_cpu_sampler_waits_for_second_load_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """Первый вызов только формирует базу, чтобы не показывать ложные 0 %."""
    loads = iter(([0.0, 0.0], [25.0, 75.0]))
    monkeypatch.setattr(cpu.psutil, "cpu_percent", lambda *, percpu: next(loads))
    monkeypatch.setattr(cpu.psutil, "cpu_freq", lambda: SimpleNamespace(current=2400.0))
    sampler = cpu.CpuSampler()

    first = sampler.sample()
    assert first.usage_percent is None
    assert first.per_core_usage_percent == ()
    assert first.frequency_mhz == 2400.0

    second = sampler.sample()
    assert second.usage_percent == 50.0
    assert second.per_core_usage_percent == (25.0, 75.0)
    assert second.frequency_mhz == 2400.0


def test_cpu_sampler_reports_source_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*, percpu: bool) -> list[float]:
        raise OSError("CPU counters unavailable")

    monkeypatch.setattr(cpu.psutil, "cpu_percent", fail)
    with pytest.raises(cpu.CpuReadError, match="CPU counters unavailable"):
        cpu.CpuSampler().sample()


def test_cpu_sampler_ignores_invalid_per_core_values(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = iter(([1.0], [10.0, -1.0, 110.0, 30.0]))
    monkeypatch.setattr(cpu.psutil, "cpu_percent", lambda *, percpu: next(samples))
    monkeypatch.setattr(cpu.psutil, "cpu_freq", lambda: None)
    sampler = cpu.CpuSampler()

    sampler.sample()
    second = sampler.sample()
    assert second.per_core_usage_percent == (10.0, 30.0)
    assert second.usage_percent == 20.0
    assert second.frequency_mhz is None
