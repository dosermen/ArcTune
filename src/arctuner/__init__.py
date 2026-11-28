"""ArcTune — Python-обёртка над IGCL API для мониторинга и разгона Intel Arc.

Предупреждение: разгон — на ваш страх и риск. См. docs/safety.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arctuner.gui.errors import GuiExceptionHandler

__version__ = "0.4.0"
__all__ = ["GuiExceptionHandler", "__version__"]


def __getattr__(name: str) -> object:
    """Ленивая подгрузка GUI-класса (PEP 562).

    Импорт пакета НЕ должен тянуть PySide6: CLI, examples и тесты работают
    без extra `gui`. При отсутствии GUI-зависимостей ошибка подсказывает
    установку.
    """
    if name == "GuiExceptionHandler":
        try:
            from arctuner.gui.errors import GuiExceptionHandler
        except ImportError as exc:  # PySide6 не установлен
            raise ImportError(
                "GuiExceptionHandler требует GUI-зависимостей: pip install 'arctune[gui]'"
            ) from exc
        return GuiExceptionHandler
    raise AttributeError(f"модуль {__name__!r} не имеет атрибута {name!r}")
