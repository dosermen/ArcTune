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


def test_core_temp_shared_memory_layout_matches_sdk() -> None:
    data = cpu._CoreTempSharedDataEx
    assert data.fTemp.offset == 1544
    assert data.ucFahrenheit.offset == 2684
    assert data.uiStructVersion.offset == 2688
    assert cpu.ctypes.sizeof(data) == 4740


def test_core_temp_temperature_conversion_and_multi_cpu_delta_mode() -> None:
    data = cpu._CoreTempSharedDataEx()
    data.uiStructVersion = 2
    data.uiCoreCnt = 2
    data.uiCPUCnt = 2
    data.fTemp[0] = 62.0
    data.fTemp[1] = 79.0
    data.fTemp[2] = 84.0
    data.fTemp[3] = 90.0
    assert cpu.CoreTempReader._temperature_from_data(data) == 90.0

    data.ucFahrenheit = 1
    data.fTemp[0] = 176.0
    data.fTemp[1] = 203.0
    data.fTemp[2] = 212.0
    data.fTemp[3] = 230.0
    assert cpu.CoreTempReader._temperature_from_data(data) == pytest.approx(110.0)

    data.ucFahrenheit = 0
    data.ucDeltaToTjMax = 1
    data.uiTjMax[0] = 100
    data.uiTjMax[1] = 105
    data.fTemp[0] = 95.0
    data.fTemp[1] = 90.0
    data.fTemp[2] = 30.0
    data.fTemp[3] = 35.0
    assert cpu.CoreTempReader._temperature_from_data(data) == 75.0

    data.uiCPUCnt = 1
    data.uiCoreCnt = 1
    data.ucFahrenheit = 1
    data.uiTjMax[0] = 212
    data.fTemp[0] = 32.0
    assert cpu.CoreTempReader._temperature_from_data(data) == pytest.approx(82.2222, rel=1e-4)


def test_core_temp_rejects_bad_shared_data() -> None:
    data = cpu._CoreTempSharedDataEx()
    data.uiStructVersion = 1
    with pytest.raises(cpu.CpuReadError, match="версия"):
        cpu.CoreTempReader._temperature_from_data(data)

    data.uiStructVersion = 2
    data.uiCoreCnt = 257
    data.uiCPUCnt = 1
    with pytest.raises(cpu.CpuReadError, match="счётчики"):
        cpu.CoreTempReader._temperature_from_data(data)

    data.uiCoreCnt = 17
    data.uiCPUCnt = 16
    with pytest.raises(cpu.CpuReadError, match="логических ядер"):
        cpu.CoreTempReader._temperature_from_data(data)


def test_core_temp_reader_copies_from_read_only_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    data = cpu._CoreTempSharedDataEx()
    data.uiStructVersion = 2
    data.uiCoreCnt = 1
    data.uiCPUCnt = 1
    data.fTemp[0] = 72.5
    closed: list[object] = []

    class FakeFunction:
        def __init__(self, func) -> None:
            self._func = func

        def __call__(self, *args):
            return self._func(*args)

    class FakeApi:
        CreateMutexW = FakeFunction(lambda *_args: 10)
        WaitForSingleObject = FakeFunction(lambda *_args: 0)
        ReleaseMutex = FakeFunction(lambda *_args: 1)
        OpenFileMappingW = FakeFunction(
            lambda _access, _inherit, name: 0 if name.startswith("Global\\") else 20
        )
        MapViewOfFile = FakeFunction(lambda *_args: cpu.ctypes.addressof(data))
        UnmapViewOfFile = FakeFunction(lambda *_args: 1)
        CloseHandle = FakeFunction(lambda handle: closed.append(handle) or 1)

    reader = cpu.CoreTempReader()
    monkeypatch.setattr(cpu.os, "name", "nt")
    monkeypatch.setattr(cpu.ctypes, "get_last_error", lambda: 2, raising=False)
    monkeypatch.setattr(reader, "_kernel32", lambda: FakeApi())

    assert reader.read_temperature() == 72.5
    assert closed == [20, 10]
