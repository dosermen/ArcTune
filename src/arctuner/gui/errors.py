"""Глобальный перехватчик необработанных исключений GUI.

PySide6 передаёт исключения из Qt-слотов в :data:`sys.excepthook`. В
windowed-exe у процесса нет консоли/stderr, поэтому без обработчика ошибка
слота (например, вызов несуществующего метода) выглядит как «кнопка ничего
не делает». Перехватчик сохраняет traceback в файл и показывает диалог.

Исключения из рабочего потока доставляются в GUI-поток сигналом: показывать
``QMessageBox`` прямо из чужого потока нельзя.
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from types import TracebackType

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from arctuner.i18n import tr
from arctuner.logbook import DEFAULT_LOG_DIR

#: Куда складываются необработанные ошибки GUI (не журнал изменений карты).
GUI_ERROR_LOG = DEFAULT_LOG_DIR / "gui-errors.log"

_Hook = Callable[[type[BaseException], BaseException, TracebackType | None], object]


def format_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> str:
    """Форматирует traceback так же, как стандартный обработчик Python.

    Args:
        exc_type: тип исключения.
        exc: объект исключения.
        tb: traceback.

    Returns:
        Текст traceback без завершающих пустых строк.
    """
    return "".join(traceback.format_exception(exc_type, exc, tb)).rstrip()


def _append_log(text: str) -> None:
    """Дописывает traceback в файл, не роняя обработчик при сбое диска."""
    try:
        GUI_ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with GUI_ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {stamp} =====\n{text}\n")
            f.flush()
    except OSError:
        # Сам обработчик ошибок не должен падать из-за недоступного файла.
        pass


class GuiExceptionHandler(QObject):
    """Показывает необработанные ошибки GUI и сохраняет их traceback.

    Экземпляр должен жить в GUI-потоке (родитель — ``QApplication``/окно).
    """

    _report = Signal(str, str)  # (кратко, traceback)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._previous_hook: _Hook | None = None
        self._seen: set[str] = set()
        # AutoConnection: ошибка GUI-потока показывается сразу, ошибка
        # рабочего потока встаёт в очередь GUI-потока (это обязательно).
        self._report.connect(self._show, Qt.ConnectionType.AutoConnection)

    def install(self) -> None:
        """Подменяет ``sys.excepthook``; повторный вызов — no-op."""
        if self._previous_hook is not None:
            return
        self._previous_hook = sys.excepthook
        sys.excepthook = self._handle

    def uninstall(self) -> None:
        """Возвращает прежний hook (нужно тестам и аккуратному завершению)."""
        if self._previous_hook is None:
            return
        sys.excepthook = self._previous_hook
        self._previous_hook = None

    def _handle(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Сохраняет ошибку и передаёт её в GUI-поток."""
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            previous = self._previous_hook or sys.__excepthook__
            previous(exc_type, exc, tb)
            return

        details = format_exception(exc_type, exc, tb)
        _append_log(details)
        summary = f"{exc_type.__name__}: {exc}"
        self._report.emit(summary, details)

    @Slot(str, str)
    def _show(self, summary: str, details: str) -> None:
        """Показывает диалог один раз на одинаковый traceback."""
        if details in self._seen:
            return
        self._seen.add(details)
        message = (
            f"{summary}\n\n"
            + tr(
                "Действие могло не выполниться. Приложение продолжит работу.",
                "The operation may not have completed. The application will continue running.",
            )
            + "\n\n"
            + tr(
                f"Полный traceback сохранён: {GUI_ERROR_LOG}",
                f"Full traceback saved to: {GUI_ERROR_LOG}",
            )
        )
        with contextlib.suppress(Exception):
            QMessageBox.critical(
                None,
                tr("ArcTune — внутренняя ошибка", "ArcTune — internal error"),
                message,
            )


__all__ = ["GUI_ERROR_LOG", "GuiExceptionHandler", "format_exception"]
