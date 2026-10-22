"""Тесты локализации (arctuner.i18n) без железа и без Qt.

Покрывают: выбор языка (env → файл → ru), персистентность файла,
tr()-fallback для непереводимых строк и смену языка на ходу.
"""

from __future__ import annotations

import pytest

from arctuner import i18n


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Изолирует файл языка (tmp) и восстанавливает русский после каждого теста.

    Язык кэшируется на уровне модуля (i18n._current), поэтому без явного
    восстановления выбор «en» протёк бы в соседние GUI/CLI-тесты.
    """
    language_file = tmp_path / "language"
    monkeypatch.setattr(i18n, "_language_file", lambda: language_file)
    monkeypatch.delenv("ARCTUNE_LANG", raising=False)
    i18n.set_language(i18n.LANG_RU)
    yield
    i18n.set_language(i18n.LANG_RU)


def test_default_is_russian() -> None:
    """Без файла и без env язык — русский (по умолчанию)."""
    assert i18n.load_language() == i18n.LANG_RU


def test_tr_returns_russian_by_default() -> None:
    """tr() отдаёт русский вариант, когда язык — ru."""
    assert i18n.tr("Вид", "View") == "Вид"


def test_tr_returns_english_when_set() -> None:
    """tr() отдаёт английский вариант после set_language(en)."""
    i18n.set_language(i18n.LANG_EN)
    assert i18n.tr("Вид", "View") == "View"


def test_tr_fallback_to_russian_when_en_none() -> None:
    """tr(ru, None) всегда русский — технические детали не переводятся."""
    i18n.set_language(i18n.LANG_EN)
    assert i18n.tr("Журнал", None) == "Журнал"


def test_save_and_load_language(tmp_path) -> None:
    """save_language пишет файл, load_language его читает."""
    i18n.save_language(i18n.LANG_EN)
    assert i18n._language_file().exists()
    assert i18n.load_language() == i18n.LANG_EN


def test_load_language_invalid_file_falls_back_to_russian() -> None:
    """Мусор в файле → русский (не исключение)."""
    i18n._language_file().parent.mkdir(parents=True, exist_ok=True)
    i18n._language_file().write_text("xx", encoding="utf-8")
    assert i18n.load_language() == i18n.LANG_RU


def test_save_language_invalid_code_falls_back_to_russian() -> None:
    """save_language с недопустимым кодом молча пишет ru."""
    i18n.save_language("zz")
    assert i18n._language_file().read_text(encoding="utf-8").strip() == i18n.LANG_RU


def test_env_overrides_file(monkeypatch) -> None:
    """ARCTUNE_LANG имеет приоритет над файлом."""
    i18n.save_language(i18n.LANG_RU)
    monkeypatch.setenv("ARCTUNE_LANG", i18n.LANG_EN)
    assert i18n.load_language() == i18n.LANG_EN


def test_current_language_uses_cache() -> None:
    """current_language() кэшируется; set_language() сбрасывает выбор."""
    i18n.set_language(i18n.LANG_EN)
    assert i18n.current_language() == i18n.LANG_EN
    i18n.set_language(i18n.LANG_RU)
    assert i18n.current_language() == i18n.LANG_RU


def test_set_language_invalid_code_normalizes_to_russian() -> None:
    """set_language с мусором нормализуется к русскому."""
    i18n.set_language("garbage")
    assert i18n.current_language() == i18n.LANG_RU


def test_language_labels_cover_all_languages() -> None:
    """LANGUAGE_LABELS покрывает все поддерживаемые языки."""
    assert set(i18n.LANGUAGE_LABELS) == set(i18n.LANGUAGES)
