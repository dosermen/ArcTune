"""Главное окно ArcTune: вкладки + таймер опроса + связь с рабочим потоком."""

from __future__ import annotations

from PySide6.QtCore import QMetaObject, Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
)

from arctuner.gui.tabs import FansTab, LogTab, MonitoringTab, OverclockTab, VfTab
from arctuner.gui.worker import GpuWorker
from arctuner.igcl.api import DeviceInfo

POLL_MS = 1000  # период опроса телеметрии; запись не опрашивается вовсе


class MainWindow(QMainWindow):
    """Окно с вкладками. Все вызовы IGCL уходят в GpuWorker (отдельный поток)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ArcTune — Intel Arc")
        self.resize(980, 700)

        self._tabs = QTabWidget()
        self._monitoring = MonitoringTab()
        self._overclock = OverclockTab()
        self._fans = FansTab()
        self._vf = VfTab()
        self._log = LogTab()
        self._tabs.addTab(self._monitoring, "Мониторинг")
        self._tabs.addTab(self._overclock, "Разгон")
        self._tabs.addTab(self._fans, "Вентиляторы")
        self._tabs.addTab(self._vf, "V/F")
        self._tabs.addTab(self._log, "Журнал")
        self.setCentralWidget(self._tabs)

        self._device_label = QLabel("Подключение…")
        self._status = QStatusBar()
        self._status.addPermanentWidget(self._device_label, stretch=1)
        self.setStatusBar(self._status)

        # --- worker в отдельном потоке: handle не потокобезопасен ---
        self._thread = QThread(self)
        self._worker = GpuWorker()
        self._worker.attach_thread(self._thread)

        self._wire_worker()
        self._wire_tabs()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._worker.poll_telemetry)

        self._thread.start()
        self._timer.start()

    # --- сигнальная разводка ---

    def _wire_worker(self) -> None:
        w = self._worker
        w.device_ready.connect(self._on_device)
        w.telemetry_ready.connect(self._monitoring.on_telemetry)
        w.oc_ready.connect(self._overclock.set_ranges)
        w.fans_ready.connect(self._fans.set_fans)
        w.vf_ready.connect(self._vf.set_curve)
        w.log_ready.connect(self._log.set_entries)
        w.write_done.connect(self._on_write_done)
        w.error.connect(self._on_error)

    def _wire_tabs(self) -> None:
        o = self._overclock
        o.apply_requested.connect(self._worker.apply)
        o.reset_requested.connect(self._worker.reset_oc)

        f = self._fans
        f.set_fan_requested.connect(self._worker.set_fan)
        f.reset_fans_requested.connect(self._worker.reset_fans)

        v = self._vf
        v.export_requested.connect(self._worker.export_vf)
        v.apply_requested.connect(self._worker.apply_vf)
        v.refresh_requested.connect(self._worker.reload_static)

        lg = self._log
        lg.rollback_requested.connect(self._worker.rollback)
        lg.refresh_requested.connect(self._worker.reload_static)

    # --- обработчики ---

    def _on_device(self, info: DeviceInfo) -> None:
        self._device_label.setText(info.name)
        self.setWindowTitle(f"ArcTune — {info.name}")

    def _on_write_done(self, message: str) -> None:
        self._status.showMessage(f"✅ {message}", 8000)

    def _on_error(self, message: str) -> None:
        """Ошибки показываем и в статусе, и диалогом — запись не должна пройти молча."""
        self._status.showMessage(f"⚠️ {message}", 15000)
        QMessageBox.warning(self, "ArcTune — ошибка", message)

    def closeEvent(self, event) -> None:  # noqa: N802 — имя Qt API
        """Останавливает опрос и закрывает IGCL-сессию в потоке воркера.

        shutdown — queued-слот воркера, поэтому вызываем BlockingQueued:
        ctlClose обязан выполниться в том же потоке, где был ctlInit, и до
        остановки QThread (иначе сессия утекает).
        """
        self._timer.stop()
        if self._thread.isRunning():
            QMetaObject.invokeMethod(
                self._worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
            )
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


__all__ = ["MainWindow"]
