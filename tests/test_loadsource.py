"""Тесты настройки источника загрузки (arctuner.loadsource), без железа.

Покрывают: выбор источника (env → файл → igcl), персистентность файла,
нормализацию мусора, кэш current_source и полноту меток источников.
"""

from __future__ import annotations

import pytest

from arctuner import loadsource


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Изолирует файл источника (tmp) и сбрасывает igcl после каждого теста.

    Источник кэшируется на уровне модуля (loadsource._current), поэтому без
    явного сброса выбор «pdh» протёк бы в соседние тесты.
    """
    source_file = tmp_path / "load_source"
    monkeypatch.setattr(loadsource, "_source_file", lambda: source_file)
    monkeypatch.delenv("ARCTUNE_LOAD_SOURCE", raising=False)
    loadsource.set_source(loadsource.SOURCE_IGCL)
    yield
    loadsource.set_source(loadsource.SOURCE_IGCL)


def test_default_is_igcl() -> None:
    """Без файла и без env источник — igcl (по умолчанию, реализован)."""
    assert loadsource.load_source() == loadsource.SOURCE_IGCL


def test_save_and_load_source() -> None:
    """save_source пишет файл, load_source его читает."""
    loadsource.save_source(loadsource.SOURCE_PDH)
    assert loadsource._source_file().exists()
    assert loadsource.load_source() == loadsource.SOURCE_PDH


def test_load_source_invalid_file_falls_back_to_igcl() -> None:
    """Мусор в файле → igcl (не исключение)."""
    loadsource._source_file().parent.mkdir(parents=True, exist_ok=True)
    loadsource._source_file().write_text("bogus", encoding="utf-8")
    assert loadsource.load_source() == loadsource.SOURCE_IGCL


def test_save_source_invalid_value_falls_back_to_igcl() -> None:
    """save_source с недопустимым значением молча пишет igcl."""
    loadsource.save_source("zzz")
    raw = loadsource._source_file().read_text(encoding="utf-8").strip()
    assert raw == loadsource.SOURCE_IGCL


def test_env_overrides_file(monkeypatch) -> None:
    """ARCTUNE_LOAD_SOURCE имеет приоритет над файлом."""
    loadsource.save_source(loadsource.SOURCE_IGCL)
    monkeypatch.setenv("ARCTUNE_LOAD_SOURCE", loadsource.SOURCE_DXGI)
    assert loadsource.load_source() == loadsource.SOURCE_DXGI


def test_current_source_uses_cache() -> None:
    """current_source() кэшируется; set_source() сбрасывает выбор."""
    loadsource.set_source(loadsource.SOURCE_PDH)
    assert loadsource.current_source() == loadsource.SOURCE_PDH
    loadsource.set_source(loadsource.SOURCE_IGCL)
    assert loadsource.current_source() == loadsource.SOURCE_IGCL


def test_set_source_invalid_normalizes_to_igcl() -> None:
    """set_source с мусором нормализуется к igcl."""
    loadsource.set_source("garbage")
    assert loadsource.current_source() == loadsource.SOURCE_IGCL


def test_source_labels_cover_all_sources() -> None:
    """SOURCE_LABELS покрывает все известные источники (для меню/CLI)."""
    assert set(loadsource.SOURCE_LABELS) == set(loadsource.SOURCES)


def test_sources_are_expected_three() -> None:
    """Ровно три источника: igcl (реализован) + pdh/dxgi (зарезервированы)."""
    assert loadsource.SOURCES == ("igcl", "pdh", "dxgi")
    assert loadsource.SOURCE_IGCL in loadsource.SOURCES
