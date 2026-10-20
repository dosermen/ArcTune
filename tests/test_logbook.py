"""Тесты журнала изменений (logbook.py) — без железа.

Проверяют запись/чтение и, главное, расчёт целей отката (rollback_targets):
каждое поле должно откатываться к САМОМУ СТАРОМУ исходному значению.
"""

from __future__ import annotations

import pytest

from arctuner.logbook import Logbook


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
