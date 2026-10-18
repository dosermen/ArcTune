"""Масштаб интерфейса GUI: чтение/сохранение и применение до создания QApplication.

Масштаб реализован через QT_SCALE_FACTOR, а не через подгонку шрифтов: Qt сам
масштабирует окна, диалоги, отступы и графики QtCharts — единственный источник
правды, без «ручного» дизайна под каждый монитор.

Важно: QT_SCALE_FACTOR читается Qt только при создании QApplication, поэтому
масштаб применяется при следующем старте приложения.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSettings

#: доступные варианты масштаба (доля от системного)
SCALE_CHOICES: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0)

_DEFAULT = 1.0


def _settings() -> QSettings:
    """Настройки приложения (ArcTune/ArcTune: реестр на Windows, ini — в тестах)."""
    return QSettings("ArcTune", "ArcTune")


def load_scale() -> float:
    """Возвращает сохранённый масштаб; если его нет или он некорректен — 1.0."""
    try:
        return float(_settings().value("ui/scale", _DEFAULT))
    except (TypeError, ValueError):
        return _DEFAULT


def save_scale(value: float) -> None:
    """Сохраняет масштаб; применяется при следующем запуске."""
    _settings().setValue("ui/scale", float(value))


def apply_qt_scale() -> float:
    """Вызывается ДО создания QApplication: ставит QT_SCALE_FACTOR из настроек.

    Returns:
        применённый масштаб (1.0 — ничего не менялось).
    """
    scale = load_scale()
    if abs(scale - _DEFAULT) > 1e-6:
        os.environ["QT_SCALE_FACTOR"] = f"{scale:g}"
    return scale


def restart_argv() -> list[str]:
    """Аргументы для перезапуска текущего приложения с новым масштабом.

    В собранном exe (PyInstaller onefile) достаточно самого исполняемого файла;
    при запуске из исходников нужен `-m arctuner.gui.main`.
    """
    if getattr(sys, "frozen", False):
        return []
    return ["-m", "arctuner.gui.main"]


__all__ = ["SCALE_CHOICES", "apply_qt_scale", "load_scale", "restart_argv", "save_scale"]
