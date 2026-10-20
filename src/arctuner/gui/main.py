"""Точка входа GUI: `arctune-gui` / `python -m arctuner.gui.main`.

Показывает главное окно с вкладками. Все вызовы IGCL идут в отдельном потоке
(GpuWorker), поэтому окно не подвисает, а запись остаётся изолированной в
tuner.py — GUI только запрашивает и показывает подтверждения.
"""

from __future__ import annotations

import sys


def run() -> int:
    """Запускает приложение. Возвращает код выхода Qt."""
    from PySide6.QtWidgets import QApplication

    from arctuner.gui.errors import GuiExceptionHandler
    from arctuner.gui.scale import apply_qt_scale
    from arctuner.gui.theme import apply_theme, load_theme
    from arctuner.gui.window import MainWindow

    # До QApplication: Qt читает QT_SCALE_FACTOR только при создании app.
    apply_qt_scale()

    app = QApplication(sys.argv)
    app.setApplicationName("ArcTune")
    app.setOrganizationName("ArcTune")

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
