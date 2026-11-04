"""Явные опции безопасности GUI: авто-откат OC при срабатывании watchdog.

Хранение — QSettings (ArcTune/ArcTune), как у темы и масштаба. Опция выключена
по умолчанию и включается пользователем осознанно: откат пишет на карту,
молча включаться не должен.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

#: ключ настройки; префикс safety/, а не ui/ — это поведение записи, а не вида.
_KEY = "safety/auto_rollback"


def _settings() -> QSettings:
    """Настройки приложения (ArcTune/ArcTune: реестр на Windows, ini — в тестах)."""
    return QSettings("ArcTune", "ArcTune")


def load_auto_rollback() -> bool:
    """Только явно сохранённое True; всё прочее (грязь, старые версии) → False."""
    # str() вместо приведения типа: бэкенд QSettings отдаёт и bool, и «true»/«1».
    return str(_settings().value(_KEY, False)).lower() in ("true", "1")


def save_auto_rollback(on: bool) -> None:
    """Сохраняет опцию: действует сразу (слот воркера) и при следующем старте."""
    _settings().setValue(_KEY, bool(on))


__all__ = ["load_auto_rollback", "save_auto_rollback"]
