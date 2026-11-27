"""Точка входа GUI: `arctune-gui` / `python -m arctuner.gui.main`.

Показывает главное окно с вкладками. Все вызовы IGCL идут в отдельном потоке
(GpuWorker), поэтому окно не подвисает, а запись остаётся изолированной в
tuner.py — GUI только запрашивает и показывает подтверждения.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def _healthcheck() -> None:
    """Проверяет наличие IGCL.dll ДО инициализации Qt.

    Если DLL нет или драйвер не установлен — показываем ошибку
    через tkinter (лёгкая, не требует Qt) и выходим.
    Иначе Qt может инициализироваться впустую.
    """
    try:
        from arctuner.igcl.loader import IgclLoader, IgclNotFoundError

        loader = IgclLoader()
        info = loader.healthcheck()
        logger.info("healthcheck: %s", info["path"])
    except IgclNotFoundError as exc:
        # tkinter — лёгкий, не требует Qt: покажет ошибку без нагрузки
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.tk.call("set", "tk_version")  # проверка, что tk жив
        messagebox.showerror(
            "ArcTune — драйвер Intel Arc",
            f"IGCL.dll не найдена.\n{exc}\n\nПроверьте, что установлен драйвер Intel Arc Graphics.",
        )
        root.destroy()
        sys.exit(1)
    except Exception as exc:
        logger.warning("healthcheck warning (non-fatal): %s", exc)


def run() -> int:
    """Запускает приложение. Возвращает код выхода Qt."""
    # Healthcheck до QApplication: не инициализируем Qt, если драйвер нет.
    _healthcheck()

    from PySide6.QtWidgets import QApplication

    from arctuner.gui.errors import GuiExceptionHandler
    from arctuner.gui.icons import app_icon
    from arctuner.gui.scale import apply_qt_scale
    from arctuner.gui.theme import apply_theme, load_theme
    from arctuner.gui.window import MainWindow

    # До QApplication: Qt читает QT_SCALE_FACTOR только при создании app.
    apply_qt_scale()

    app = QApplication(sys.argv)
    app.setApplicationName("ArcTune")
    app.setOrganizationName("ArcTune")
    app.setWindowIcon(app_icon())

    # В windowed-exe stderr нет: без этого исключение слота теряется молча.
    # Родитель — app: обработчик живёт до конца процесса.
    error_handler = GuiExceptionHandler(app)
    error_handler.install()

    # Fusion + палитра + QSS до создания окна: вид не зависит от стиля Windows.
    apply_theme(app, load_theme() == "dark")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
