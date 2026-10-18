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

    from arctuner.gui.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ArcTune")
    app.setOrganizationName("ArcTune")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
