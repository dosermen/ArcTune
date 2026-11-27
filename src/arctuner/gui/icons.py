"""Иконки приложения ArcTune."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

APP_ICON_PATH = Path(__file__).parent / "assets" / "arctune.ico"


def app_icon() -> QIcon:
    """Возвращает иконку приложения из ресурса пакета."""
    return QIcon(str(APP_ICON_PATH))


__all__ = ["APP_ICON_PATH", "app_icon"]
