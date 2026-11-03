"""Главное окно ArcTune: вкладки + таймер опроса + связь с рабочим потоком."""

from __future__ import annotations

import sys
from typing import cast

from PySide6.QtCharts import QChartView
from PySide6.QtCore import QMetaObject, QProcess, Qt, QThread, QTimer, Slot
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

from arctuner.device_profile import profile_status_note
from arctuner.gui.charts import ThemedChart
from arctuner.gui.overlay import (
    LAYOUTS,
    OPACITIES,
    OverlayWindow,
    layout_label,
    load_overlay_enabled,
)
from arctuner.gui.scale import (
    SCALE_CHOICES,
    load_scale,
    restart_argv,
    restart_env,
    save_scale,
)
from arctuner.gui.tabs import (
    AutoTuneTab,
    FansTab,
    LogTab,
    MonitoringTab,
    OverclockTab,
    OverlayTab,
    VfTab,
)
from arctuner.gui.theme import apply_theme, flame_icon, load_theme, save_theme
from arctuner.gui.worker import GpuWorker
from arctuner.i18n import (
    LANGUAGE_LABELS,
    LANGUAGES,
    current_language,
    save_language,
    set_language,
    tr,
)
from arctuner.igcl.api import DeviceInfo, ExtendedDeviceInfo

POLL_MS = 1000  # период опроса телеметрии; запись не опрашивается вовсе


class MainWindow(QMainWindow):
    """Окно с вкладками. Все вызовы IGCL уходят в GpuWorker (отдельный поток)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ArcTune — Intel Arc")
        self.resize(980, 700)

        # Оверлей (HUD поверх окон) — создаётся первым: работают с ним и вкладка
        # «Оверлей», и меню «Вид». Без родителя — чтобы Tool-окно не пряталось
        # вместе с главным (HUD нужен при свёрнутом ArcTune).
        self._overlay = OverlayWindow()
        if load_overlay_enabled():
            self._overlay.show()

        self._tabs = QTabWidget()
        self._monitoring = MonitoringTab()
        self._overclock = OverclockTab()
        self._fans = FansTab()
        self._vf = VfTab()
        self._log = LogTab()
        self._autotune = AutoTuneTab()
        self._overlay_tab = OverlayTab(self._overlay)  # настройки HUD (без IGCL)
        style = self.style()  # иконки вкладок и окна — стандартные Qt, без ресурсов
        self.setWindowIcon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        for index, (widget, icon_kind, name) in enumerate(
            (
                (
                    self._monitoring,
                    style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                    tr("Мониторинг", "Monitoring"),
                ),
                (
                    self._overclock,
                    flame_icon(),
                    tr("Разгон", "Overclock"),
                ),  # огонь вместо чёрной стрелки
                (
                    self._fans,
                    style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                    tr("Вентиляторы", "Fans"),
                ),
                (
                    self._vf,
                    style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
                    "V/F",
                ),
                (
                    self._log,
                    style.standardIcon(QStyle.StandardPixmap.SP_FileIcon),
                    tr("Журнал", "Journal"),
                ),
                (
                    self._autotune,
                    style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                    tr("Авторазгон", "AutoTune"),
                ),
                (
                    self._overlay_tab,
                    style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon),
                    tr("Оверлей", "Overlay"),
                ),
            )
        ):
            self._tabs.addTab(widget, name)
            self._tabs.setTabIcon(index, icon_kind)
        self.setCentralWidget(self._tabs)

        self._device_label = QLabel(tr("Подключение…", "Connecting…"))
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
        # Асинхронное закрытие: окно не ждёт воркера (он может быть занят
        # engine.run/watchdog), а завершает поток по сигналу shutdown_done.
        self._closing = False

        # Оверлей создан в начале __init__ (нужен и вкладке, и меню «Вид»);
        # телеметрия приходит из _wire_worker.
        self._build_view_menu()

        # --- worker в отдельном потоке: handle не потокобезопасен ---
        self._thread = QThread(self)
        self._worker = GpuWorker()
        self._worker.attach_thread(self._thread)

        self._build_info_menu()
        self._wire_worker()
        self._wire_tabs()
        # Поток завершился (штатно или после shutdown_done) — повторить close.
        self._thread.finished.connect(self._on_thread_finished)
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
        w.device_info_ready.connect(self._on_device_info)
        w.telemetry_ready.connect(self._monitoring.on_telemetry)
        # Оверлей читает те же снимки (без второго опроса IGCL): 1 Гц в обычном
        # режиме и ~2 Гц через autotune_sample, пока идёт поиск.
        w.telemetry_ready.connect(self._overlay.on_telemetry)
        w.oc_ready.connect(self._overclock.set_ranges)
        w.fans_ready.connect(self._fans.set_fans)
        w.vf_ready.connect(self._vf.set_curve)
        w.log_ready.connect(self._log.set_entries)
        w.write_done.connect(self._on_write_done)
        w.error.connect(self._on_error)
        w.write_error.connect(self._on_write_error)
        w.busy_changed.connect(self._on_busy)
        w.incident_replay.connect(self._on_incident)
        w.shutdown_done.connect(self._on_shutdown_done)
        w.autotune_progress.connect(self._autotune.update_progress)
        # Живая телеметрия поиска: общий график «Мониторинга» + мини-график
        # на вкладке «Авторазгон» (иначе оба замирали на время engine.run).
        w.autotune_sample.connect(self._monitoring.on_telemetry)
        w.autotune_sample.connect(self._autotune.on_sample)
        w.autotune_sample.connect(self._overlay.on_telemetry)
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

        m = self._monitoring
        m.export_requested.connect(self._worker.export_telemetry_csv)

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
        """Меню «Вид»: масштаб (100–200%), тема и оверлей.

        QT_SCALE_FACTOR читается при старте, поэтому переключение масштаба
        сохраняется и применяется после перезапуска; тема и оверлей применяются
        немедленно. Выбор макета/непрозрачности дублируется в вкладке «Оверлей»
        и в контекстном меню самого оверлея — все три дороги сходятся на одних
        настройках и синхронизируются сигналами OverlayWindow.
        """
        menu = self.menuBar().addMenu(tr("Вид", "View"))
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
        self._restart_action = QAction(tr("Перезапустить приложение", "Restart application"), self)
        self._restart_action.setEnabled(False)
        self._restart_action.triggered.connect(self._on_restart)
        menu.addAction(self._restart_action)

        menu.addSeparator()
        theme_menu = menu.addMenu(tr("Тема", "Theme"))
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        current_theme = load_theme()
        self._theme_actions: dict[str, QAction] = {}
        for name, label in (
            ("dark", tr("Тёмная", "Dark")),
            ("light", tr("Светлая", "Light")),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(name == current_theme)
            action.triggered.connect(lambda _=False, n=name: self._on_theme_selected(n))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[name] = action

        menu.addSeparator()
        lang_menu = menu.addMenu(tr("Язык", "Language"))
        lang_group = QActionGroup(self)
        lang_group.setExclusive(True)
        current_lang = current_language()
        self._lang_actions: dict[str, QAction] = {}
        for code in LANGUAGES:
            action = QAction(LANGUAGE_LABELS[code], self, checkable=True)
            action.setChecked(code == current_lang)
            action.triggered.connect(lambda _=False, c=code: self._on_language_selected(c))
            lang_group.addAction(action)
            lang_menu.addAction(action)
            self._lang_actions[code] = action

        menu.addSeparator()
        overlay_menu = menu.addMenu(tr("Оверлей", "Overlay"))
        self._overlay_toggle = QAction(
            tr("Показывать поверх окон", "Always on top"), self, checkable=True
        )
        self._overlay_toggle.setChecked(load_overlay_enabled())
        self._overlay_toggle.triggered.connect(self._on_overlay_toggled)
        overlay_menu.addAction(self._overlay_toggle)

        layout_menu = overlay_menu.addMenu(tr("Макет", "Layout"))
        layout_group = QActionGroup(self)
        layout_group.setExclusive(True)
        self._overlay_layout_actions: dict[str, QAction] = {}
        for name in LAYOUTS:
            action = QAction(layout_label(name), self, checkable=True)
            action.setChecked(name == self._overlay.layout_name())
            action.triggered.connect(lambda _=False, n=name: self._overlay.set_layout(n))
            layout_group.addAction(action)
            layout_menu.addAction(action)
            self._overlay_layout_actions[name] = action

        opacity_menu = overlay_menu.addMenu(tr("Непрозрачность", "Opacity"))
        opacity_group = QActionGroup(self)
        opacity_group.setExclusive(True)
        self._overlay_opacity_actions: dict[float, QAction] = {}
        for value in OPACITIES:
            action = QAction(f"{value * 100:g} %", self, checkable=True)
            action.setChecked(abs(value - self._overlay.current_opacity()) < 1e-3)
            action.triggered.connect(lambda _=False, v=value: self._overlay.set_opacity(v))
            opacity_group.addAction(action)
            opacity_menu.addAction(action)
            self._overlay_opacity_actions[value] = action

        # Контекстное меню оверлея и вкладка «Оверлей» меняют те же настройки —
        # галочки меню синхронизируем обратным сигналом (setChecked не эмитит
        # triggered — зацикливания нет).
        self._overlay.layout_changed.connect(self._sync_overlay_layout)
        self._overlay.opacity_changed.connect(self._sync_overlay_opacity)
        self._overlay.visibility_changed.connect(self._sync_overlay_visibility)

    def _build_info_menu(self) -> None:
        """Меню «Информация»: обновление данных об устройстве."""
        info_menu = self.menuBar().addMenu(tr("Информация", "Information"))
        self._info_refresh_action = QAction(tr("Обновить информацию", "Refresh info"), self)
        self._info_refresh_action.triggered.connect(self._on_info_refresh)
        info_menu.addAction(self._info_refresh_action)

    @Slot()
    def _on_info_refresh(self) -> None:
        """Перенаправляет вызов обновления в воркер-поток через queued-слот.

        Прямой вызов метода воркера из UI-потока выполнял бы чтение IGCL
        прямо в GUI-потоке (заморозка интерфейса) — invokeMethod гарантирует
        исполнение в потоке воркера.
        """
        QMetaObject.invokeMethod(
            self._worker, "poll_device_info", Qt.ConnectionType.QueuedConnection
        )

    def _on_scale_selected(self, value: float) -> None:
        save_scale(value)
        self._restart_action.setEnabled(True)
        self._status.showMessage(
            tr(
                f"Масштаб {value * 100:g}% сохранён — перезапустите приложение "
                f"(меню «Вид» → «Перезапустить приложение»).",
                f"Scale {value * 100:g}% saved — restart the application "
                f"(menu «View» → «Restart application»).",
            ),
            10000,
        )

    def _on_language_selected(self, lang: str) -> None:
        """Сохраняет язык и предлагает перезапуск (применяется при следующем старте).

        Подписи создаются при построении виджетов, поэтому — как и масштаб —
        язык применяется только после перезапуска. Выбор пишется в тот же файл
        (~/.arctune/language), который читает и CLI.

        set_language() переключает язык процесса сразу, чтобы сам диалог (и
        дальнейшие сообщения) уже отображались на выбранном языке — это же
        показывает пользователю, что переключение работает.
        """
        if lang == current_language():
            return
        save_language(lang)
        set_language(lang)
        self._restart_action.setEnabled(True)
        answer = QMessageBox.question(
            self,
            tr("Язык интерфейса", "Interface language"),
            tr(
                "Язык сохранён. Перезапустить приложение сейчас?\n\n"
                "Изменения применяются после перезапуска.",
                "Language saved. Restart the application now?\n\nChanges apply after restart.",
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._on_restart()
        else:
            self._status.showMessage(
                tr(
                    "Язык применится после перезапуска («Вид» → «Перезапустить приложение»).",
                    "The language will apply after a restart («View» → «Restart application»).",
                ),
                10000,
            )

    def _on_theme_selected(self, name: str) -> None:
        """Пересобирает палитру/QSS на ходу.

        График мониторинга не трогаем (LiveChart.set_dark — намеренный no-op,
        сохранённый вид по требованию владельца); кривая V/F подстраивается.
        """
        save_theme(name)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, name == "dark")
        self._apply_theme_to_charts(name == "dark")

    def _apply_theme_to_charts(self, dark: bool) -> None:
        """Синхронизирует темы всех графиков (мониторинг игнорирует — см. выше)."""
        for view in self.findChildren(QChartView):
            # Все реальные view — LiveChart/VfChart, оба реализуют set_dark;
            # findChildren возвращает базовый QChartView без этого метода.
            cast("ThemedChart", view).set_dark(dark)

    # --- оверлей (HUD поверх окон) ---

    def _on_overlay_toggled(self, checked: bool) -> None:
        """Вкл/выкл оверлея из меню «Вид» (сохранение — внутри оверлея).

        Оверлей — единая точка истины: set_shown правит настройку, окно и
        эмитит visibility_changed, поэтому вкладка «Оверлей» сама подхватит
        изменение (визитки «Вид» и вкладки не расходятся).
        """
        self._overlay.set_shown(checked)

    @Slot(str)
    def _sync_overlay_layout(self, name: str) -> None:
        """Макет сменён из контекстного меню оверлея — ставим галочку в «Вид»."""
        for key, action in self._overlay_layout_actions.items():
            action.setChecked(key == name)

    @Slot(float)
    def _sync_overlay_opacity(self, value: float) -> None:
        """Непрозрачность сменена из контекстного меню — то же для меню «Вид»."""
        for key, action in self._overlay_opacity_actions.items():
            action.setChecked(abs(key - value) < 1e-3)

    @Slot(bool)
    def _sync_overlay_visibility(self, shown: bool) -> None:
        """Показ/скрытие из любой точки (вкладка, правый клик) — галочка меню."""
        self._overlay_toggle.setChecked(shown)

    def _on_restart(self) -> None:
        """Перезапускает приложение, чтобы применить сохранённый масштаб или язык.

        В onefile-сборке дочерний процесс СТАРТУЕТ СВОЙ `_MEIxxxx`: стартеру
        передаётся окружение без переменных PyInstaller (restart_env), иначе
        новый bootloader переиспользует временный каталог умирающего инстанса
        и падает с «Failed to import encodings module».
        """
        proc = QProcess()
        proc.setProgram(sys.executable)
        proc.setArguments(restart_argv())
        proc.setProcessEnvironment(restart_env())
        if proc.startDetached():
            self.close()  # closeEvent корректно закроет сессию IGCL в потоке воркера
        else:
            QMessageBox.information(
                self, "ArcTune", "Не удалось перезапустить автоматически — запустите вручную."
            )

    # --- обработчики ---

    def _on_device(self, info: DeviceInfo) -> None:
        self._device_seen = True
        note = profile_status_note(info.device_id)
        label = f"{info.name} ⚠️" if note else info.name
        self._device_label.setText(label)
        self._device_label.setToolTip(note)
        self.setWindowTitle(f"ArcTune — {info.name}")

    @Slot(object, object)
    def _on_device_info(
        self,
        info: DeviceInfo,
        extended: ExtendedDeviceInfo,
    ) -> None:
        """Обработка расширенной информации об устройстве.

        Отображает имя, ID, драйвер, прошивку, частоту и Xe-ядра в статус-баре.
        """
        status_text = (
            f"📊 {info.name} | "
            f"ID: {info.device_id:#06X} | "
            f"Driver: {extended.driver_version_str} | "
            f"Firmware: {extended.firmware_version_str} | "
            f"Freq: {info.frequency_mhz} МГц | "
            f"Xe-cores: {info.num_xe_cores}"
        )
        note = profile_status_note(info.device_id)
        if note:
            status_text += f" | ⚠️ {note}"
        self._status.showMessage(status_text, 5000)

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
            self._device_label.setText(tr("Нет подключения", "No connection"))
            self._timer.stop()
            QMessageBox.warning(
                self,
                tr("ArcTune — нет подключения", "ArcTune — no connection"),
                tr(
                    f"Не удалось подключиться к Intel Arc:\n{message}\n\n"
                    "Опрос остановлен. Запустите приложение заново после проверки "
                    "драйвера/подключения карты.",
                    f"Failed to connect to Intel Arc:\n{message}\n\n"
                    "Polling stopped. Restart the application after checking the "
                    "driver/card connection.",
                ),
            )

    def _on_write_error(self, message: str) -> None:
        """Ошибка записи — модально: запись не должна пройти молча."""
        self._set_pill("warn", f"⚠️ {message}")
        QMessageBox.warning(self, tr("ArcTune — ошибка записи", "ArcTune — write error"), message)

    def _on_autotune_error(self, message: str) -> None:
        """Ошибка процесса авторазгона (не одиночная запись)."""
        self._set_pill("warn", f"⚠️ авторазгон: {message}")
        QMessageBox.warning(
            self, tr("ArcTune — ошибка авторазгона", "ArcTune — auto-tune error"), message
        )

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
        if self._closing:
            return  # окно закрывается: не воскрешаем остановленный таймер
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
        """Закрывает IGCL-сессию в потоке воркера, не блокируя UI.

        Порядок: остановить таймер → попросить поиск остановиться → поставить
        shutdown в очередь воркера. Закрытие окна откладывается (event.ignore):
        ctlClose обязан выполниться в том же потоке, где был ctlInit, и ДО
        остановки QThread. Когда воркер сообщит shutdown_done, поток
        завершается, а _on_thread_finished повторяет close — теперь без ожидания.

        UI при этом остаётся отзывчивым, даже если воркер занят engine.run или
        watchdog (PowerShell/taskkill): queued-слот выполнится после текущей
        операции, окно показывает «Завершение…» и не висит. Повторное нажатие
        X безопасно: shutdown ставится в очередь один раз.
        """
        self._timer.stop()
        self._overlay.close()  # HUD — не дожидаемся конца shutdown (окно не должно пережить приложение)
        if not self._thread.isRunning():
            super().closeEvent(event)  # потока нет — закрываемся обычным путём
            return
        # Честный стоп поиска: без него queued shutdown ждал бы конца engine.run.
        self._worker.autotune_stop()
        if not self._closing:
            self._closing = True
            self._status.showMessage("Завершение: закрываем сессию IGCL…")
            QMetaObject.invokeMethod(self._worker, "shutdown", Qt.ConnectionType.QueuedConnection)
        event.ignore()

    @Slot()
    def _on_shutdown_done(self) -> None:
        """ctlClose выполнен в потоке воркера — можно останавливать QThread."""
        if not self._closing:
            return  # ручной shutdown() (тесты/повтор) — окно не закрывается
        self._thread.quit()

    @Slot()
    def _on_thread_finished(self) -> None:
        """Поток воркера остановлен: повторяем close — теперь он принимается."""
        if not self._closing:
            return
        self.close()


__all__ = ["MainWindow"]
