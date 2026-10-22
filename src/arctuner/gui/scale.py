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

from PySide6.QtCore import QProcessEnvironment, QSettings

#: доступные варианты масштаба (доля от системного)
SCALE_CHOICES: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0)

_DEFAULT = 1.0


def _settings() -> QSettings:
    """Настройки приложения (ArcTune/ArcTune: реестр на Windows, ini — в тестах)."""
    return QSettings("ArcTune", "ArcTune")


def load_scale() -> float:
    """Возвращает сохранённый масштаб; если его нет или он некорректен — 1.0.

    Признаются только значения из SCALE_CHOICES: произвольный мусор в реестре
    (скринридер, чужая программа, опечатка) не должен задавать QT_SCALE_FACTOR.
    """
    try:
        scale = float(_settings().value("ui/scale", _DEFAULT))
    except (TypeError, ValueError):
        return _DEFAULT
    return next(
        (choice for choice in SCALE_CHOICES if abs(choice - scale) < 1e-3),
        _DEFAULT,
    )


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
    else:
        # 100%: переменную надо УДАЛИТЬ, а не просто не выставлять. Иначе
        # унаследованное от родителя значение (перезапуск из GUI) применится
        # снова, и масштаб 100% не сработает.
        os.environ.pop("QT_SCALE_FACTOR", None)
    return scale


def restart_argv() -> list[str]:
    """Аргументы для перезапуска текущего приложения с новым масштабом.

    В собранном exe (PyInstaller onefile) достаточно самого исполняемого файла;
    при запуске из исходников нужен `-m arctuner.gui.main`.
    """
    if getattr(sys, "frozen", False):
        return []
    return ["-m", "arctuner.gui.main"]


def restart_env() -> QProcessEnvironment:
    """Окружение для перезапуска без переменных PyInstaller.

    В onefile-сборке сам процесс несёт `_PYI_APPLICATION_HOME_DIR`
    (и легаси `_MEIPASS2`), указывающий на его `_MEIxxxx`. Если дочерний
    процесс унаследует их, его bootloader примет это за «извлечение уже
    сделано» и запустится из ЧУЖОГО (и к тому же удаляемого родителем)
    временного каталога — старт падает с «Failed to import encodings
    module», а старый процесс не может удалить `_MEIxxxx`. Убираем все
    `_PYI*` — новый экземпляр распакуется сам.
    """
    env = QProcessEnvironment.systemEnvironment()
    for key in list(env.keys()):
        if key.startswith("_PYI") or key == "_MEIPASS2":
            env.remove(key)
    return env


__all__ = [
    "SCALE_CHOICES",
    "apply_qt_scale",
    "load_scale",
    "restart_argv",
    "restart_env",
    "save_scale",
]
