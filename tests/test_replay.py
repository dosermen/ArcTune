"""Тесты реплея инцидента: кольцевой буфер, детект сброса счётчика, дамп, ротация.

Время передаётся явно (t / wall) — тесты детерминированы, без сна.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arctuner.igcl.api import TelemetrySnapshot
from arctuner.replay import IncidentReplay


def _snap(
    gpu_j: float | None = 1000.0,
    card_j: float | None = 5000.0,
    vram_j: float | None = 100.0,
) -> TelemetrySnapshot:
    """Минимальный снимок: нужны только счётчики энергии."""
    return TelemetrySnapshot(
        gpu_voltage_v=1.0,
        gpu_freq_mhz=2500.0,
        gpu_temp_c=55.0,
        gpu_power_percent=70.0,
        vram_voltage_v=1.35,
        vram_eff_freq_mbps=19.75,
        vram_temp_c=48.0,
        power_limited=False,
        temperature_limited=False,
        current_limited=False,
        voltage_limited=False,
        utilization_limited=False,
        activity_counter=42,
        gpu_energy_counter_j=gpu_j,
        card_energy_counter_j=card_j,
        vram_energy_counter_j=vram_j,
    )


def _read_dump(path: Path) -> tuple[dict, list[dict]]:
    """Читает дамп: (первая строка = meta, остальные = записи)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])["meta"]
    rows = [json.loads(x) for x in lines[1:]]
    return meta, rows


def test_push_and_dump_roundtrip(tmp_path: Path) -> None:
    """Дамп = meta + все снимки; t отсчитывается от начала буфера."""
    replay = IncidentReplay(directory=tmp_path, device="B580")
    replay.push(_snap(), t=0.0, wall=1700000000.0)
    replay.push(_snap(gpu_j=1010.0), t=1.0, wall=1700000001.0)
    path = replay.dump("manual_interrupt")
    assert path is not None and path.exists()
    meta, rows = _read_dump(path)
    assert meta["trigger"] == "manual_interrupt"
    assert meta["samples"] == 2
    assert meta["device"] == "B580"
    assert meta["started_at"].startswith("2023-")  # 1700000000 = ноя 2023 UTC
    assert len(rows) == 2
    assert rows[0]["t"] == 0.0
    assert rows[1]["t"] == 1.0
    assert rows[1]["gpu_energy_counter_j"] == 1010.0
    assert rows[1]["gpu_freq_mhz"] == 2500.0


def test_energy_counter_reset_detected(tmp_path: Path) -> None:
    """Счётчик стал меньше предыдущего → триггер сброса (драйвер перезагрузился)."""
    replay = IncidentReplay(directory=tmp_path)
    replay.push(_snap(gpu_j=1000.0), t=0.0)
    assert replay.push(_snap(gpu_j=900.0), t=1.0) == "energy_counter_reset_gpu"
    # Card/vram — тем же правилом, каждая пара — свой триггер.
    replay2 = IncidentReplay(directory=tmp_path / "b")
    replay2.push(_snap(card_j=5000.0), t=0.0)
    assert replay2.push(_snap(card_j=4999.0), t=1.0) == "energy_counter_reset_card"
    replay3 = IncidentReplay(directory=tmp_path / "c")
    replay3.push(_snap(vram_j=100.0), t=0.0)
    assert replay3.push(_snap(vram_j=50.0), t=1.0) == "energy_counter_reset_vram"


def test_growing_counter_no_trigger(tmp_path: Path) -> None:
    """Нормальный рост счётчика и отсутствие значения — инцидентов нет."""
    replay = IncidentReplay(directory=tmp_path)
    assert replay.push(_snap(gpu_j=1000.0), t=0.0) is None
    assert replay.push(_snap(gpu_j=1050.0), t=1.0) is None
    assert replay.push(_snap(gpu_j=None), t=2.0) is None  # счётчик пропал — не сброс


def test_dump_empty_buffer_is_none(tmp_path: Path) -> None:
    """Дамп без данных — None: пустой дамп это ложь, не создаём файл."""
    replay = IncidentReplay(directory=tmp_path)
    assert replay.dump("manual_interrupt") is None


def test_dump_clears_buffer_keeps_baseline(tmp_path: Path) -> None:
    """После дампа буфер пуст, но база счётчика жива: сброс после дампа ловится."""
    replay = IncidentReplay(directory=tmp_path)
    replay.push(_snap(gpu_j=1000.0), t=0.0)
    path = replay.dump("manual_interrupt")
    assert path is not None
    assert replay.samples == 0
    # База 1000 осталась: 500 < 1000 — снова инцидент.
    assert replay.push(_snap(gpu_j=500.0), t=1.0) == "energy_counter_reset_gpu"


def test_ring_buffer_trims_old_samples(tmp_path: Path) -> None:
    """Буфер ограничен: старые снимки выбрасываются (deque maxlen)."""
    replay = IncidentReplay(max_samples=3, directory=tmp_path)
    for i in range(6):
        replay.push(_snap(gpu_j=1000.0 + i), t=float(i))
    assert replay.samples == 3
    path = replay.dump("manual_interrupt")
    meta, rows = _read_dump(path)  # type: ignore[arg-type]
    assert meta["samples"] == 3
    # Оставшиеся 3 — последние (t=3,4,5); t в дампе — от начала буфера: 0,1,2.
    assert [r["t"] for r in rows] == [0.0, 1.0, 2.0]
    assert [r["gpu_energy_counter_j"] for r in rows] == [1003.0, 1004.0, 1005.0]


def test_rotation_keeps_recent_files(tmp_path: Path) -> None:
    """Ротация: остаются последние keep_files дампов, старые удаляются."""
    replay = IncidentReplay(directory=tmp_path, keep_files=2)
    for name in (
        "incident_20260101_000001_manual.jsonl",
        "incident_20260101_000002_manual.jsonl",
        "incident_20260101_000003_manual.jsonl",
    ):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    replay.push(_snap(), t=0.0)
    replay.dump("manual_interrupt")
    names = sorted(p.name for p in tmp_path.glob("incident_*.jsonl"))
    assert len(names) == 2
    # 000001 удалён; свежий дамп (сегодня) и 000003 остаются.
    assert names[0] == "incident_20260101_000003_manual.jsonl"
    assert names[1].startswith("incident_2026")


def test_two_dumps_in_one_second_get_unique_files(tmp_path: Path, monkeypatch) -> None:
    """Два инцидента подряд (в одну секунду) — два файла, оба валидный JSONL."""
    from datetime import datetime as real_datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 12, 0, 0, 123456, tzinfo=tz)

    # Морозим часы имени: два дампа получают одинаковые микросекунды.
    monkeypatch.setattr("arctuner.replay.datetime", _FrozenDatetime)

    replay = IncidentReplay(directory=tmp_path, device="B580")
    replay.push(_snap(), t=0.0, wall=1700000000.0)
    first = replay.dump("manual_interrupt")
    replay.push(_snap(gpu_j=1010.0), t=1.0, wall=1700000001.0)
    second = replay.dump("manual_interrupt")

    assert first is not None and second is not None
    assert first != second
    assert first.exists() and second.exists()
    for path in (first, second):
        meta, rows = _read_dump(path)
        assert meta["trigger"] == "manual_interrupt"
        assert len(rows) == 1
    assert replay.samples == 0


def test_dump_failure_keeps_buffer_and_removes_tmp(tmp_path: Path, monkeypatch) -> None:
    """Сбой os.replace: финального файла нет, буфер цел, временных *.tmp нет."""
    replay = IncidentReplay(directory=tmp_path)
    replay.push(_snap(), t=0.0, wall=1700000000.0)
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("os.replace упал")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace"):
        replay.dump("manual_interrupt")

    assert replay.samples == 1  # контекст инцидента не потерян
    assert list(tmp_path.glob("incident_*.jsonl")) == []
    assert list(tmp_path.glob("*.tmp")) == []

    # Повторный дамп после сбоя пишет тот же буфер в новый файл.
    monkeypatch.setattr(os, "replace", real_replace)
    path = replay.dump("manual_interrupt")
    assert path is not None and path.exists()
    assert replay.samples == 0
