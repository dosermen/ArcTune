"""Оверлей ArcTune: компактный HUD поверх окон (read-only).

Мини-панель с живой телеметрией, всегда поверх других окон — видна поверх
игр и бенчмарков в оконном/безрамочном режиме. Ограничение ОС, а не проекта:
в эксклюзивном fullscreen Windows не показывает поверх оверлеи сторонних
процессов.

Выбор пользователя сохраняется в настройках (ArcTune/ArcTune — тот же файл,
что масштаб и тема):

* ``overlay/enabled``   — включён ли оверлей (меню «Вид → Оверлей»);
* ``overlay/layout``    — макет: полоса 1×4, сетка 2×2, подробный 2×4;
* ``overlay/opacity``   — непрозрачность (50/70/85/100 %);
* ``overlay/geometry``  — положение и размер (перетаскивание мышью).

Инварианты проекта: окно строго read-only — ни одной кнопки записи; данные
приходят сигналами GpuWorker (``telemetry_ready`` 1 Гц и ``autotune_sample``
~2 Гц во время поиска) — второго конкурентного опроса IGCL нет.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, Qt, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from arctuner.igcl.api import TelemetrySnapshot

#: макеты HUD (выбор пользователя; хранится в настройках)
LAYOUTS: tuple[str, ...] = ("compact", "grid", "detailed")

#: подписи макетов для меню (главного окна и контекстного меню оверлея)
LAYOUT_LABELS: dict[str, str] = {
    "compact": "Полоса 1×4",
    "grid": "Сетка 2×2",
    "detailed": "Подробный 2×4",
}

#: допустимая непрозрачность (доля от 1.0)
OPACITIES: tuple[float, ...] = (0.5, 0.7, 0.85, 1.0)

DEFAULT_LAYOUT: str = "compact"
DEFAULT_OPACITY: float = 0.85

#: (ключ ячейки, подпись, единица, accent цвет значения)
_CELLS: tuple[tuple[str, str, str, str | None], ...] = (
    ("temp", "Температура", "°C", "temp"),
    ("power", "Питание", "Вт", "power"),
    ("freq", "Ядро", "МГц", "freq"),
    ("vram", "Память", "Гбит/с", "freq"),
    ("vram_temp", "Память, T", "°C", "temp"),
    ("gpu_vr", "VR ядра", "°C", "temp"),
    ("vram_vr", "VR памяти", "°C", "temp"),
    ("sa_vr", "VR SA", "°C", "temp"),
)

#: макет → (порядок ключей ячеек, число колонок)
_LAYOUT_SPECS: dict[str, tuple[tuple[str, ...], int]] = {
    "compact": (("temp", "power", "freq", "vram"), 4),
    "grid": (("temp", "power", "freq", "vram"), 2),
    "detailed": (
        ("temp", "power", "freq", "vram", "vram_temp", "gpu_vr", "vram_vr", "sa_vr"),
        4,
    ),
}


def _settings() -> QSettings:
    """Настройки приложения (ArcTune/ArcTune: реестр на Windows, ini — в тестах)."""
    return QSettings("ArcTune", "ArcTune")


def _as_bool(raw: object, default: bool = False) -> bool:
    """QSettings-значение в bool; «false» из ini не должен читаться как True."""
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


def load_overlay_enabled() -> bool:
    """Оверлей включён ли пользователем (по умолчанию — выключен)."""
    return _as_bool(_settings().value("overlay/enabled"), default=False)


def save_overlay_enabled(value: bool) -> None:
    """Сохраняет включение/выключение оверлея (применяется сразу вызывающим)."""
    _settings().setValue("overlay/enabled", bool(value))


def load_overlay_layout() -> str:
    """Макет из настроек; мусор/отсутствие принимается за дефолт (полоса)."""
    value = _settings().value("overlay/layout", DEFAULT_LAYOUT)
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def save_overlay_layout(name: str) -> None:
    """Сохраняет макет; применяется немедленно ``OverlayWindow.set_layout``."""
    if name not in LAYOUTS:
        raise ValueError(f"неизвестный макет: {name!r} (ждём {LAYOUTS})")
    _settings().setValue("overlay/layout", name)


def load_overlay_opacity() -> float:
    """Непрозрачность из настроек; неизвестное значение схлопывается к допустимым."""
    try:
        value = float(_settings().value("overlay/opacity", DEFAULT_OPACITY))
    except (TypeError, ValueError):
        return DEFAULT_OPACITY
    return next(
        (choice for choice in OPACITIES if abs(choice - value) < 1e-3),
        DEFAULT_OPACITY,
    )


def save_overlay_opacity(value: float) -> None:
    """Сохраняет непрозрачность; распознаются только значения из OPACITIES."""
    matched = next((choice for choice in OPACITIES if abs(choice - value) < 1e-3), None)
    if matched is None:
        raise ValueError(f"непрозрачность вне {OPACITIES}: {value!r}")
    _settings().setValue("overlay/opacity", matched)


class _Cell(QFrame):
    """Одна метрика HUD: крупное значение и мелкая подпись под ним.

    Компактная версия ``MetricCard``: без внутренних отступов и рамки карточки
    (в оверлее их роль играет общий фон ``overlayRoot``), но та же логика
    форматирования и те же цвета акцентов (``overlayValue[accent=…]`` в QSS).
    """

    def __init__(
        self,
        title: str,
        unit: str,
        accent: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("overlayCell")
        self._unit = unit
        self._value = QLabel("—")
        self._value.setObjectName("overlayValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if accent is not None:
            self._value.setProperty("accent", accent)  # QSS красит значение
        self._title = QLabel(title)
        self._title.setObjectName("overlayTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        lay.addWidget(self._value)
        lay.addWidget(self._title)

    def set_value(
        self,
        value: float | int | None,
        digits: int = 0,
        *,
        unit: str | None = None,
        tooltip: str = "",
    ) -> None:
        """Показывает значение; None -> «—» (датчик недоступен).

        Args:
            value: значение датчика; None — нет данных.
            digits: знаков после запятой (для float).
            unit: переопределить единицу (по умолчанию — подпись ячейки).
            tooltip: подпись при наведении (вторичная информация).
        """
        self._value.setToolTip(tooltip)
        if value is None:
            self._value.setText("—")
            return
        shown = self._unit if unit is None else unit
        if isinstance(value, float):
            if value == 0.0:
                value = 0.0  # −0.0 печаталось как «-0.000» и смущало
            text = f"{value:.{digits}f}"
        else:
            text = str(value)
        self._value.setText(f"{text} {shown}".rstrip())

    def text(self) -> str:
        """Текущая строка значения (для тестов и диагностики)."""
        return self._value.text()


class OverlayWindow(QWidget):
    """HUD поверх окон: без рамки, всегда сверху, перетаскивается мышью.

    Правый клик — контекстное меню (макет, непрозрачность, скрыть): оверлей
    самодостаточен и не требует открытого главного окна. Смена настроек
    эмитит сигналы, чтобы меню «Вид» главного окна оставалось в согласии.
    """

    layout_changed = Signal(str)  # макет изменён (из контекстного меню)
    opacity_changed = Signal(float)  # непрозрачность изменена
    hide_requested = Signal()  # пользователь попросил спрятать оверлей

    def __init__(self, parent: QWidget | None = None) -> None:
        # Без родителя: Tool-окно не должно прятаться вместе с главным окном —
        # HUD нужен именно тогда, когда ArcTune свёрнут. Закрывается в
        # MainWindow.closeEvent.
        super().__init__(parent)
        self.setObjectName("overlayWindow")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("ArcTune — оверлей")

        self._layout = load_overlay_layout()
        self._opacity = load_overlay_opacity()
        self.setWindowOpacity(self._opacity)
        self._drag_offset: QPoint | None = None

        self._cells: dict[str, _Cell] = {}
        for key, title, unit, accent in _CELLS:
            self._cells[key] = _Cell(title, unit, accent)

        self._limits = QLabel("")
        self._limits.setObjectName("overlayLimits")
        self._limits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._limits.setProperty("active", "false")
        self._limits.setToolTip(
            "Активный лимитер прямо сейчас: Power / Temperature / Current / Voltage / "
            "Utilization Limited. «нет (запас есть)» — ничего не ограничивает."
        )

        root = QFrame(self)
        root.setObjectName("overlayRoot")
        self._grid = QGridLayout()
        self._grid.setSpacing(4)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(6, 5, 6, 5)
        root_lay.setSpacing(4)
        root_lay.addLayout(self._grid)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(root)

        self._rebuild()
        self._restore_geometry()

    # --- настройки ---

    def layout_name(self) -> str:
        """Текущий макет (см. LAYOUTS)."""
        return self._layout

    def current_opacity(self) -> float:
        """Текущая непрозрачность (см. OPACITIES)."""
        return self._opacity

    def set_layout(self, name: str) -> None:
        """Переключает макет: сохраняет выбор и пересобирает сетку ячеек."""
        if name not in LAYOUTS:
            raise ValueError(f"неизвестный макет: {name!r} (ждём {LAYOUTS})")
        if name == self._layout:
            return
        self._layout = name
        save_overlay_layout(name)
        self._rebuild()
        self.layout_changed.emit(name)

    def set_opacity(self, value: float) -> None:
        """Меняет непрозрачность окна; распознаются только значения из OPACITIES."""
        matched = next((choice for choice in OPACITIES if abs(choice - value) < 1e-3), None)
        if matched is None:
            raise ValueError(f"непрозрачность вне {OPACITIES}: {value!r}")
        if abs(matched - self._opacity) < 1e-6:
            return
        self._opacity = matched
        save_overlay_opacity(matched)
        self.setWindowOpacity(matched)
        self.opacity_changed.emit(matched)

    def hide_overlay(self) -> None:
        """Прячет оверлей и сообщает об этом (меню «Вид» снимет галочку)."""
        self.hide()
        self.hide_requested.emit()

    # --- данные ---

    @Slot(object)
    def on_telemetry(self, snap: TelemetrySnapshot) -> None:
        """Обновляет ячейки по новому снимку (слот воркера: 1 Гц, ~2 Гц в поиске)."""
        if not self.isVisible():
            return  # спрятан — перерисовка ни к чему
        self._cells["temp"].set_value(snap.gpu_temp_c)
        # Ватты — средняя из счётчиков энергии (PowerMeter); до 2-го тика —
        # честный процент от лимита, как на вкладке «Мониторинг».
        if snap.gpu_power_w is not None:
            pct = "" if snap.gpu_power_percent is None else f"{snap.gpu_power_percent:g} %"
            self._cells["power"].set_value(snap.gpu_power_w, unit="Вт", tooltip=pct)
        else:
            self._cells["power"].set_value(snap.gpu_power_percent, unit="%")
        self._cells["freq"].set_value(snap.gpu_freq_mhz)
        self._cells["vram"].set_value(snap.vram_eff_freq_mbps, digits=2)
        self._cells["vram_temp"].set_value(snap.vram_temp_c)
        self._cells["gpu_vr"].set_value(snap.gpu_vr_temp_c)
        self._cells["vram_vr"].set_value(snap.vram_vr_temp_c)
        self._cells["sa_vr"].set_value(snap.sa_vr_temp_c)
        if self._layout == "detailed":
            self._set_limits(snap)

    def _set_limits(self, snap: TelemetrySnapshot) -> None:
        """Плашка ограничений: жёлтая только когда что-то реально мешает карте."""
        active = any(
            (
                snap.power_limited,
                snap.temperature_limited,
                snap.current_limited,
                snap.voltage_limited,
                snap.utilization_limited,
            )
        )
        self._limits.setText(f"Лимит: {snap.limit_summary()}")
        self._limits.setProperty("active", "true" if active else "false")
        style = self._limits.style()
        style.unpolish(self._limits)  # динамическое property требует пере-полировки
        style.polish(self._limits)

    # --- раскладка ---

    def _rebuild(self) -> None:
        """Пересобирает сетку под текущий макет (ячейки создаются один раз)."""
        while self._grid.count():
            item = self._grid.takeAt(0)  # изымает ячейку, но оставляет её ребёнком
            widget = item.widget()
            if widget is not None:
                widget.hide()
        keys, cols = _LAYOUT_SPECS[self._layout]
        for index, key in enumerate(keys):
            cell = self._cells[key]
            self._grid.addWidget(cell, index // cols, index % cols)
            cell.show()
        if self._layout == "detailed":
            self._grid.addWidget(self._limits, len(keys) // cols, 0, 1, cols)
            self._limits.show()
        self.adjustSize()

    def _restore_geometry(self) -> None:
        """Сохранённое положение; без него — правый верхний угол основного экрана."""
        raw = _settings().value("overlay/geometry")
        restored = False
        if raw is not None:
            try:
                restored = self.restoreGeometry(raw)
            except TypeError:
                restored = False  # повреждённое значение не должно ронять старт
        if restored:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() + 1 - 16, area.top() + 16)

    def _save_geometry(self) -> None:
        """Запоминает положение/размер (вызывать только когда окно показано).

        Видимость — осознанная защита: закрытие спрятанного окна (тесты, старт
        без оверлея) не должно затирать реальное положение из реестра.
        """
        if not self.isVisible():
            return
        _settings().setValue("overlay/geometry", self.saveGeometry())

    # --- перетаскивание и контекстное меню ---

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (имя Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (имя Qt API)
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (имя Qt API)
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._save_geometry()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 (Qt API)
        """Правый клик: быстрый выбор макета/непрозрачности без главного окна."""
        menu = QMenu(self)

        layout_menu = menu.addMenu("Макет")
        layout_group = QActionGroup(menu)
        layout_group.setExclusive(True)
        for name in LAYOUTS:
            action = QAction(LAYOUT_LABELS[name], menu, checkable=True)
            action.setChecked(name == self._layout)
            action.triggered.connect(lambda _=False, n=name: self.set_layout(n))
            layout_group.addAction(action)
            layout_menu.addAction(action)

        opacity_menu = menu.addMenu("Непрозрачность")
        opacity_group = QActionGroup(menu)
        opacity_group.setExclusive(True)
        for value in OPACITIES:
            action = QAction(f"{value * 100:g} %", menu, checkable=True)
            action.setChecked(abs(value - self._opacity) < 1e-3)
            action.triggered.connect(lambda _=False, v=value: self.set_opacity(v))
            opacity_group.addAction(action)
            opacity_menu.addAction(action)

        menu.addSeparator()
        hide_action = QAction("Скрыть", menu)
        hide_action.triggered.connect(self.hide_overlay)
        menu.addAction(hide_action)

        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:  # noqa: N802 (имя Qt API)
        """Запоминает положение перед закрытием (макет/непрозрачность уже сохранены)."""
        self._save_geometry()
        super().closeEvent(event)


__all__ = [
    "DEFAULT_LAYOUT",
    "DEFAULT_OPACITY",
    "LAYOUTS",
    "LAYOUT_LABELS",
    "OPACITIES",
    "OverlayWindow",
    "load_overlay_enabled",
    "load_overlay_layout",
    "load_overlay_opacity",
    "save_overlay_enabled",
    "save_overlay_layout",
    "save_overlay_opacity",
]
