"""Тесты темы GUI: QSS-генерация, палитры, хранение выбора (без IGCL, без окна).

QSS собирается строкой в theme.py, поэтому проверяется как обычный текст;
QSettings подменяется фейком — реестр в тестах не трогается.
"""

from __future__ import annotations

import os
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6", reason="GUI-тесты требуют PySide6 (extra gui)")

from arctuner.gui import theme  # noqa: E402


def test_qss_contains_semantic_targets() -> None:
    """В обоих темах есть селекторы всех стилизованных objectNames."""
    for dark in (True, False):
        sheet = theme.qss(dark)
        assert sheet and len(sheet) > 1000
        for selector in (
            "QFrame#metricCard",
            '[accent="temp"]',
            '[accent="power"]',
            '[accent="freq"]',
            '[accent="load"]',
            '[accent="vram"]',
            "QLabel#metricValue",
            "QLabel#overlayValue",
            '[accent="load"]',
            '[accent="vram"]',
            'QLabel#overlayValue[accent="load"]',
            'QLabel#overlayValue[accent="vram"]',
            "QLabel#hintLabel",
            "QLabel#limitsLabel",
            "QPushButton#dangerButton",
            "QLabel#statePill",
            '[kind="ok"]',
            '[kind="warn"]',
            '[kind="danger"]',
        ):
            assert selector in sheet, f"{selector!r} нет в теме (dark={dark})"


class _FakeSettings:
    """Подмена QSettings: значения хранятся в словаре процесса."""

    _store: ClassVar[dict[str, object]] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def value(self, key: str, default: object = None) -> object:
        return self._store.get(key, default)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802 (Qt-имя)
        self._store[key] = value


@pytest.fixture()
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    _FakeSettings._store.clear()
    monkeypatch.setattr(theme, "QSettings", _FakeSettings)
    return _FakeSettings()


def test_theme_roundtrip(fake_settings: _FakeSettings) -> None:
    """Сохранение/чтение темы и выбор по умолчанию: мусор = тёмная."""
    assert theme.load_theme() == theme.DEFAULT_THEME
    theme.save_theme("light")
    fake_settings._store["ui/theme"] = "белиберда"  # прошлая установка могла...
    assert theme.load_theme() == theme.DEFAULT_THEME  # ...восстановлено дефолтом
    fake_settings._store["ui/theme"] = "light"
    assert theme.load_theme() == "light"


def test_save_theme_rejects_unknown() -> None:
    """Неизвестное имя темы — ValueError, в реестр мусор не пишется."""
    with pytest.raises(ValueError, match=r"неизвестная тема"):
        theme.save_theme("midnight")


def test_series_colors_are_shared_constants() -> None:
    """Цвета линий графиков: charts.py импортирует их оттуда же (единый источник)."""
    assert theme.SERIES_TEMP.startswith("#")
    assert theme.ACCENT_BY_KIND["temp"] == theme.SERIES_TEMP
    assert theme.ACCENT_BY_KIND["power"] == theme.SERIES_POWER
    assert theme.ACCENT_BY_KIND["freq"] == theme.SERIES_FREQ
    assert theme.ACCENT_BY_KIND["load"] == theme.SERIES_LOAD
    assert theme.ACCENT_BY_KIND["vram"] == theme.SERIES_VRAM
    # Линии одного графика должны различаться глазом — все пять цветов уникальны.
    colors = {
        theme.SERIES_TEMP,
        theme.SERIES_POWER,
        theme.SERIES_FREQ,
        theme.SERIES_LOAD,
        theme.SERIES_VRAM,
    }
    assert len(colors) == 5
    # charts.py берёт цвета отсюда, а не дублирует литералы.
    from arctuner.gui import charts

    assert charts._LOAD_COLOR.name() == theme.SERIES_LOAD
    assert charts._VRAM_COLOR.name() == theme.SERIES_VRAM
