"""Тесты журнала изменений (logbook.py) — без железа.

Проверяют запись/чтение и, главное, расчёт целей отката (rollback_targets):
каждое поле должно откатываться к САМОМУ СТАРОМУ исходному значению.
"""

from __future__ import annotations

import pytest

from arctuner.logbook import (
    LEGACY_RESET_OC_FIELD,
    RESET_FANS_FIELD,
    RESET_OC_FIELD,
    JournalWriteError,
    Logbook,
)


def test_empty_logbook(tmp_path) -> None:
    log = Logbook(tmp_path / "log.jsonl")
    assert log.read_all() == []
    assert log.latest_values() == {}
    assert log.rollback_targets() == {}


def test_record_and_read(tmp_path) -> None:
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0, device="B580", note="тест")
    entries = log.read_all()
    assert len(entries) == 1
    assert entries[0].field == "powerLimit"
    assert entries[0].old == 100.0
    assert entries[0].new == 110.0
    assert entries[0].device == "B580"
    assert entries[0].restored is False


def test_latest_values_takes_last(tmp_path) -> None:
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0)
    log.record("powerLimit", 110.0, 120.0)
    assert log.latest_values()["powerLimit"] == 120.0


def test_rollback_targets_use_oldest_old(tmp_path) -> None:
    """Откат идёт к самому старому `old`, а не к предыдущему `new`."""
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0)
    log.record("powerLimit", 110.0, 120.0)
    log.record("gpuFrequencyOffset", 0.0, 500.0)
    targets = log.rollback_targets()
    assert targets["powerLimit"] == 100.0  # исходное, а не 110.0
    assert targets["gpuFrequencyOffset"] == 0.0


def test_mark_restored(tmp_path) -> None:
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0)
    log.mark_restored("powerLimit")
    assert log.read_all()[0].restored is True


def test_rewrite_atomic_on_failure(tmp_path, monkeypatch) -> None:
    """Сбой подмены файла не оставляет пустой журнал: старая история цела.

    Регрессия: mark_restored открывал файл в режиме "w" (обнуление до записи) —
    сбой посреди перезаписи терял ВСЮ историю изменений.
    """
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0)
    before = log.path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("диск отвалился")

    monkeypatch.setattr("arctuner.logbook.os.replace", boom)
    with pytest.raises(OSError):
        log.mark_restored("powerLimit")
    assert log.path.read_text(encoding="utf-8") == before  # журнал цел
    assert list(tmp_path.glob("*.tmp")) == []  # временный файл убран


def test_tail_order_newest_first(tmp_path) -> None:
    log = Logbook(tmp_path / "log.jsonl")
    log.record("powerLimit", 100.0, 110.0)
    log.record("gpuFrequencyOffset", 0.0, 500.0)
    tail = log.tail(5)
    assert tail[0].field == "gpuFrequencyOffset"  # новее первым
    assert tail[1].field == "powerLimit"


def test_skips_corrupt_lines(tmp_path) -> None:
    p = tmp_path / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        '{"field":"powerLimit","old":100,"new":110,"timestamp":"t",'
        '"device":"","note":"","restored":false}\n'
        "{битая строка}\n",
        encoding="utf-8",
    )
    log = Logbook(p)
    entries = log.read_all()
    assert len(entries) == 1  # битая строка пропущена, чтение не упало
    assert entries[0].field == "powerLimit"


class TestResetMarkers:
    """Сброс инвалидирует старые цели отката, но история сохраняется."""

    def test_reset_oc_clears_previous_targets(self, tmp_path) -> None:
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record("gpuFrequencyOffset", 0.0, 50.0)
        log.record(RESET_OC_FIELD, None, None, note="сброс")
        assert log.rollback_targets() == {}
        # История не потеряна: маркер виден, старые записи на месте.
        fields = [e.field for e in log.read_all()]
        assert fields == ["powerLimit", "gpuFrequencyOffset", RESET_OC_FIELD]

    def test_new_record_after_reset_is_the_target(self, tmp_path) -> None:
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record(RESET_OC_FIELD, None, None)
        log.record("powerLimit", 120.0, 110.0)
        targets = log.rollback_targets()
        assert targets["powerLimit"] == 120.0  # запись ПОСЛЕ сброса

    def test_legacy_all_marker_cuts_history(self, tmp_path) -> None:
        """Старые журналы писали field="all" — тоже считаем сбросом OC."""
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record(LEGACY_RESET_OC_FIELD, None, None)
        assert log.rollback_targets() == {}
        log.record("powerLimit", 120.0, 115.0)
        assert log.rollback_targets()["powerLimit"] == 120.0

    def test_latest_values_clear_oc_on_reset(self, tmp_path) -> None:
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record("fan[0]", 1200.0, 1800.0)
        log.record(RESET_OC_FIELD, None, None)
        vals = log.latest_values()
        assert "powerLimit" not in vals
        assert vals["fan[0]"] == 1800.0  # сброс OC не трогает вентиляторы

    def test_reset_fans_marker_clears_fan_values(self, tmp_path) -> None:
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record("fan[0]", 1200.0, 1800.0)
        log.record(RESET_FANS_FIELD, None, None)
        vals = log.latest_values()
        assert "fan[0]" not in vals
        assert vals["powerLimit"] == 120.0
        # Вентиляторы не цель отката ни до, ни после маркера.
        assert "fan[0]" not in log.rollback_targets()

    def test_mark_restored_still_works_with_markers(self, tmp_path) -> None:
        log = Logbook(tmp_path / "log.jsonl")
        log.record("powerLimit", 100.0, 120.0)
        log.record(RESET_OC_FIELD, None, None)
        log.record("powerLimit", 120.0, 110.0)
        log.mark_restored("powerLimit")
        restored = [e.restored for e in log.read_all() if e.field == "powerLimit"]
        assert restored == [False, True]


def test_journal_write_error_is_oserror() -> None:
    """JournalWriteError наследуется от OSError: CLI/GUI уже умеют её показывать."""
    err = JournalWriteError("журнал недоступен")
    assert isinstance(err, OSError)
    assert str(err) == "журнал недоступен"


def test_compute_rollback_targets_standalone(tmp_path) -> None:
    """compute_rollback_targets работает как standalone (не требует Logbook)."""
    from arctuner.logbook import LogEntry, compute_rollback_targets

    entries = [
        LogEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            field="powerLimit",
            old=100.0,
            new=110.0,
        ),
        LogEntry(
            timestamp="2026-01-01T00:01:00+00:00",
            field="gpuFrequencyOffset",
            old=0.0,
            new=200.0,
        ),
        LogEntry(
            timestamp="2026-01-01T00:02:00+00:00",
            field="fan[0]",
            old=None,
            new=1500.0,
        ),
    ]
    targets = compute_rollback_targets(entries)
    assert targets["powerLimit"] == 100.0
    assert targets["gpuFrequencyOffset"] == 0.0
    assert "fan[0]" not in targets


def test_compute_rollback_targets_respects_reset(tmp_path) -> None:
    """Reset-маркер очищает цели: записи до него не входят."""
    from arctuner.logbook import RESET_OC_FIELD, LogEntry, compute_rollback_targets

    entries = [
        LogEntry(
            timestamp="2026-01-01T00:00:00+00:00",
            field="powerLimit",
            old=100.0,
            new=110.0,
        ),
        LogEntry(
            timestamp="2026-01-01T00:01:00+00:00",
            field=RESET_OC_FIELD,
            old=None,
            new=None,
        ),
        LogEntry(
            timestamp="2026-01-01T00:02:00+00:00",
            field="powerLimit",
            old=110.0,
            new=105.0,
        ),
    ]
    targets = compute_rollback_targets(entries)
    assert targets == {"powerLimit": 110.0}  # после сброса
