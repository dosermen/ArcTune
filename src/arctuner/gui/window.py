"""Главное окно ArcTune: вкладки + таймер опроса + связь с рабочим потоком."""

from __future__ import annotations

import sys

from PySide6.QtCharts import QChartView
from PySide6.QtCore import QMetaObject, QProcess, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QStyle,
    QTabWidget,
)

from arctuner.gui.scale import SCALE_CHOICES, load_scale, restart_argv, save_scale
from arctuner.gui.tabs import AutoTuneTab, FansTab, LogTab, MonitoringTab, OverclockTab, VfTab
from arctuner.gui.theme import apply_theme, flame_icon, load_theme, save_theme
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
        self._autotune = AutoTuneTab()
        style = self.style()  # иконки вкладок и окна — стандартные Qt, без ресурсов
        self.setWindowIcon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        for index, (widget, icon_kind, name) in enumerate(
            (
                (
                    self._monitoring,
                    style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                    "Мониторинг",
                ),
                (self._overclock, flame_icon(), "Разгон"),  # огонь вместо чёрной стрелки
                (
                    self._fans,
                    style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                    "Вентиляторы",
                ),
                (
                    self._vf,
                    style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
                    "V/F",
                ),
                (self._log, style.standardIcon(QStyle.StandardPixmap.SP_FileIcon), "Журнал"),
                (
                    self._autotune,
                    style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                    "Авторазгон",
                ),
            )
        ):
            self._tabs.addTab(widget, name)
            self._tabs.setTabIcon(index, icon_kind)
        self.setCentralWidget(self._tabs)

        self._device_label = QLabel("Подключение…")
        self._status = QStatusBar()
        self._status.addPermanentWidget(self._device_label, stretch=1)
        # Пилюля состояния вместо безликого текста: ok (запись) / warn / danger (инцидент).
        self._pill = QLabel("")
        self._pill.setObjectName("statePill")
        self._pill.hide()
        self._status.addPermanentWidget(self._pill)
        self.setStatusBar(self._status)

        # Устройство ещё не подтвердилось: первый error = провал старта
        # («Подключение…» навсегда недопустим — останавливаем опрос и показываем).
        self._device_seen = False

        self._build_view_menu()

        # --- worker в отдельном потоке: handle не потокобезопасен ---
        self._thread = QThread(self)
        self._worker = GpuWorker()
        self._worker.attach_thread(self._thread)

        self._wire_worker()
        self._wire_tabs()
        # Кривая V/F стартует в текущей теме (график мониторинга игнорирует — no-op).
        self._apply_theme_to_charts(load_theme() == "dark")

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
        w.write_error.connect(self._on_write_error)
        w.busy_changed.connect(self._on_busy)
        w.incident_replay.connect(self._on_incident)
        w.autotune_progress.connect(self._autotune.update_progress)
        # Живая телеметрия поиска: общий график «Мониторинга» + мини-график
        # на вкладке «Авторазгон» (иначе оба замирали на время engine.run).
        w.autotune_sample.connect(self._monitoring.on_telemetry)
        w.autotune_sample.connect(self._autotune.on_sample)
        w.autotune_completed.connect(self._autotune.display_result)
        w.autotune_error.connect(self._on_autotune_error)

    def _wire_tabs(self) -> None:
        o = self._overclock
        o.apply_requested.connect(self._worker.apply)
        o.reset_requested.connect(self._worker.reset_oc)

        f = self._fans
        f.set_fan_requested.connect(self._worker.set_fan)
        f.reset_fans_requested.connect(self._worker.reset_fans)
        f.refresh_requested.connect(self._worker.reload_fans)

        v = self._vf
        v.export_requested.connect(self._worker.export_vf)
        v.apply_requested.connect(self._worker.apply_vf)
        v.refresh_requested.connect(self._worker.reload_static)

        lg = self._log
        lg.rollback_requested.connect(self._worker.rollback)
        lg.refresh_requested.connect(self._worker.reload_static)

        at = self._autotune
        at.search_requested.connect(self._on_autotune_start)
        # Stop — напрямой вызов: queued-слот не выполнился бы, пока воркер-поток
        # занят синхронным поиском (флаг читается поиском без обработки очереди).
        at.stop_requested.connect(self._worker.autotune_stop, Qt.ConnectionType.DirectConnection)

    # --- меню «Вид»: масштаб интерфейса ---

    def _build_view_menu(self) -> None:
        """Меню масштаба: 100–200%. QT_SCALE_FACTOR читается при старте, поэтому
        переключение сохраняется и применяется после перезапуска."""
        menu = self.menuBar().addMenu("Вид")
        group = QActionGroup(self)
        group.setExclusive(True)

        current = load_scale()
        self._scale_actions: dict[float, QAction] = {}
        for value in SCALE_CHOICES:
            action = QAction(f"{value * 100:g} %", self, checkable=True)
            action.setChecked(abs(value - current) < 1e-6)
            action.triggered.connect(lambda _=False, v=value: self._on_scale_selected(v))
            group.addAction(action)
            menu.addAction(action)
            self._scale_actions[value] = action

        menu.addSeparator()
        self._restart_action = QAction("Перезапустить с новым масштабом", self)
        self._restart_action.setEnabled(False)
        self._restart_action.triggered.connect(self._on_restart)
        menu.addAction(self._restart_action)

        menu.addSeparator()
        theme_menu = menu.addMenu("Тема")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        current = load_theme()
        self._theme_actions: dict[str, QAction] = {}
        for name, label in (("dark", "Тёмная"), ("light", "Светлая")):
            action = QAction(label, self, checkable=True)
            action.setChecked(name == current)
            action.triggered.connect(lambda _=False, n=name: self._on_theme_selected(n))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[name] = action

    def _on_scale_selected(self, value: float) -> None:
        save_scale(value)
        self._restart_action.setEnabled(True)
        self._status.showMessage(
            f"Масштаб {value * 100:g}% сохранён — перезапустите приложение "
            f"(меню «Вид» → «Перезапустить с новым масштабом»).",
            10000,
        )

    def _on_theme_selected(self, name: str) -> None:
        """Пересобирает палитру/QSS на ходу.

        График мониторинга не трогаем (LiveChart.set_dark — намеренный no-op,
        сохранённый вид по требованию владельца); кривая V/F подстраивается.
        """
        save_theme(name)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, name == "dark")
        self._apply_theme_to_charts(name == "dark")

    def _apply_theme_to_charts(self, dark: bool) -> None:
        """Синхронизирует темы всех графиков (мониторинг игнорирует — см. выше)."""
        for view in self.findChildren(QChartView):
            view.set_dark(dark)

    def _on_restart(self) -> None:
        """Перезапускает приложение, чтобы применить сохранённый масштаб."""
        if QProcess.startDetached(sys.executable, restart_argv()):
            self.close()  # closeEvent корректно закроет сессию IGCL в потоке воркера
        else:
            QMessageBox.information(
                self, "ArcTune", "Не удалось перезапустить автоматически — запустите вручную."
            )

    # --- обработчики ---

    def _on_device(self, info: DeviceInfo) -> None:
        self._device_seen = True
        self._device_label.setText(info.name)
        self.setWindowTitle(f"ArcTune — {info.name}")

    def _set_pill(self, kind: str, text: str) -> None:
        """Стилизованная пилюля состояния вместо безликого showMessage-текста.

        kind: ok (запись прошла) / warn (сбой чтения, ошибка записи) /
        danger (инцидент). Держится до следующего события — последний итог записи
        всегда виден, побочных таймаутов нет (showMessage остаётся для подсказок).
        """
        self._pill.setProperty("kind", kind)
        self._pill.setText(text)
        self._pill.show()
        style = self._pill.style()
        style.unpolish(self._pill)  # динамическое property требует пере-полировки
        style.polish(self._pill)

    def _on_write_done(self, message: str) -> None:
        self._set_pill("ok", f"✅ {message}")

    def _on_error(self, message: str) -> None:
        """Ошибка чтения/старта.

        До подключения (device_ready не пришёл) — провалился старт: останавливаем
        опрос (иначе он будет повторять ошибку каждую секунду) и показываем причину
        диалогом. После — кратковременный сбой телеметрии виден только в пилюле
        (warn), чтобы модальные окна не сыпались по одному на каждый тик.
        """
        self._set_pill("warn", f"⚠️ {message}")
        if not self._device_seen:
            self._device_label.setText("Нет подключения")
            self._timer.stop()
            QMessageBox.warning(
                self,
                "ArcTune — нет подключения",
                f"Не удалось подключиться к Intel Arc:\n{message}\n\n"
                "Опрос остановлен. Запустите приложение заново после проверки "
                "драйвера/подключения карты.",
            )

    def _on_write_error(self, message: str) -> None:
        """Ошибка записи — модально: запись не должна пройти молча."""
        self._set_pill("warn", f"⚠️ {message}")
        QMessageBox.warning(self, "ArcTune — ошибка записи", message)

    def _on_autotune_error(self, message: str) -> None:
        """Ошибка процесса авторазгона (не одиночная запись)."""
        self._set_pill("warn", f"⚠️ авторазгон: {message}")
        QMessageBox.warning(self, "ArcTune — ошибка авторазгона", message)

    def _on_autotune_start(self, config: object) -> None:
        """Старт авторазгона: сброс флага отмены синхронно, затем queued-слот.

        Разделение важно: если сброс делать внутри слота, «Стоп», нажатый пока
        слот стоит в очереди (воркер занят другой операцией), будет молча
        проигнорирован — слот сбросит флаг и запустит полный поиск.
        """
        self._worker.autotune_arm()
        self._worker.autotune_start.emit(config)

    def _on_incident(self, path: str) -> None:
        """Инцидент: счётчик энергии сбросился = драйвер перезагрузился (TDR/ребут).

        Не модально (модальное окно собьёт живые графики в момент самого события),
        в пилюле danger и надолго; полный путь — в дампе.
        """
        self._set_pill("danger", f"🚨 Инцидент (драйвер перезагрузился?) — дамп: {path}")

    def _on_busy(self, busy: bool) -> None:
        """Воркер занят записью/поиском: блокируем кнопки и гасим опрос.

        Опрос во время поиска мешал бы: воркер-поток и так занят (queued-вызовы
        копились и выстреляли флудом после). Живой график при этом не стоит —
        движок отдаёт снимки через autotune_sample на каждом чтении телеметрии.
        """
        if busy:
            self._timer.stop()  # поиск/запись: опрос не нужен и вреден
        else:
            self._timer.start()
        self._overclock.set_busy(busy)
        self._fans.set_busy(busy)
        self._vf.set_busy(busy)
        self._log.set_busy(busy)
        self._autotune.set_busy(busy)

    def closeEvent(self, event) -> None:  # noqa: N802 — имя Qt API
        """Останавливает опрос и закрывает IGCL-сессию в потоке воркера.

        shutdown — queued-слот воркера, поэтому вызываем BlockingQueued:
        ctlClose обязан выполниться в том же потоке, где был ctlInit, и до
        остановки QThread (иначе сессия утекает).
        """
        self._timer.stop()
        if self._thread.isRunning():
            # Честный стоп поиска: без него shutdown ждал бы многоминутного
            # engine.run (воркер-поток занят), и окно висело бы «не отвечает».
            self._worker.autotune_stop()
            QMetaObject.invokeMethod(
                self._worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
            )
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


__all__ = ["MainWindow"]
