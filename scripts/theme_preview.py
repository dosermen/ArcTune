"""Одноразовый визуальный смок темы: offscreen-скриншоты главного окна.

Не тест (запускается вручную): открывает главное окно с живым воркером
(IGCL на реальной машине), снимает PNG вкладок в temp, ГОСТ выключает.
"""

from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from arctuner.gui.theme import apply_theme, load_theme
from arctuner.gui.window import MainWindow

# Снимки — в системный temp (персональные пути в публичном репозитории не нужны).
OUT = tempfile.gettempdir()
SHOTS: list[tuple[str, int]] = [
    ("tune_monitoring.png", 0),
    ("tune_overclock.png", 1),
    ("tune_vf.png", 3),
    ("tune_log.png", 4),
]


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app, load_theme() == "dark")
    window = MainWindow()
    window.show()
    tabs = window._tabs
    state = {"i": 0}

    def shoot() -> None:
        if state["i"] >= len(SHOTS):
            app.quit()
            return
        name, index = SHOTS[state["i"]]
        state["i"] += 1
        tabs.setCurrentIndex(index)
        QTimer.singleShot(80, lambda: (window.grab().save(os.path.join(OUT, name)), shoot()))

    # watchdog: даже если откажется рендер — скрипт не висит вечно
    QTimer.singleShot(30000, app.quit)
    QTimer.singleShot(2500, shoot)
    app.exec()


if __name__ == "__main__":
    sys.exit(main())
