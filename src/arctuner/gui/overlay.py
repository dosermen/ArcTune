"""Оверлей ArcTune: компактный HUD поверх окон (read-only).

Мини-панель с живой телеметрией, всегда поверх других окон — видна поверх
игр и бенчмарков в оконном/безрамочном режиме. Ограничение ОС, а не проекта:
в эксклюзивном fullscreen Windows не показывает поверх оверлеи сторонних
процессов.

Выбор пользователя сохраняется в настройках (ArcTune/ArcTune — тот же файл,
что масштаб и тема):

* ``overlay/enabled``   — включён ли оверлей (вкладка «Оверлей», меню «Вид»);
* ``overlay/layout``    — макет: полоса 1×6, сетка 3×2, подробный 2×5;
* ``overlay/cells``     — какие метрики показывать (можно снять/поставить
  галочки вручную — пресет макета возвращает свой набор);
* ``overlay/opacity``   — непрозрачность (50/70/85/100 %);
* ``overlay/geometry``  — положение и размер (перетаскивание мышью).

Настройки доступны отовсюду: вкладка «Оверлей», подменю «Вид → Оверлей» и
правый клик по самому HUD — все три дороги ведут в один ``OverlayWindow`` и
синхронизируются его сигналами.

Инварианты проекта: окно строго read-only — ни одной кнопки записи; данные
приходят сигналами GpuWorker (``telemetry_ready`` 1 Гц и ``autotune_sample``
~2 Гц во время поиска) — второго конкурентного опроса IGCL нет.
"""

from __future__ import annotations

from collections.abc import Iterable

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

from arctuner.i18n import tr
from arctuner.igcl.api import TelemetrySnapshot

#: макеты HUD (выбор пользователя; хранится в настройках)
LAYOUTS: tuple[str, ...] = ("compact", "grid", "detailed")

#: подписи макетов для меню (главного окна и контекстного меню оверлея);
#: размер честный: строки × колонки под текущий пресет метрик
LAYOUT_LABELS: dict[str, str] = {
    "compact": "Полоса 1×6",
    "grid": "Сетка 3×2",
    "detailed": "Подробный 2×5",
}

#: английские подписи макетов (см. LAYOUT_LABELS)
LAYOUT_LABELS_EN: dict[str, str] = {
    "compact": "Bar 1×6",
    "grid": "Grid 3×2",
    "detailed": "Detailed 2×5",
}


def layout_label(name: str) -> str:
    """Подпись макета на текущем языке интерфейса (см. arctuner.i18n)."""
    return tr(LAYOUT_LABELS[name], LAYOUT_LABELS_EN[name])


#: допустимая непрозрачность (доля от 1.0)
OPACITIES: tuple[float, ...] = (0.5, 0.7, 0.85, 1.0)

DEFAULT_LAYOUT: str = "compact"
DEFAULT_OPACITY: float = 0.85

#: (ключ ячейки, подпись, единица, accent цвет значения)
_CELLS: tuple[tuple[str, str, str, str | None], ...] = (
    ("temp", "Температура", "°C", "temp"),
    ("power", "Питание", "Вт", "power"),
    ("freq", "Ядро", "МГц", "freq"),
    ("load", "Загрузка", "%", "load"),
    ("vram", "Память", "Гбит/с", "freq"),
    ("vram_used", "Память занято", "ГиБ", "vram"),
    ("vram_temp", "Память, T", "°C", "temp"),
    ("gpu_vr", "VR ядра", "°C", "temp"),
    ("vram_vr", "VR памяти", "°C", "temp"),
    ("sa_vr", "VR SA", "°C", "temp"),
)

#: макет → (порядок ключей ячеек, число колонок)
#: Пресеты подписаны честно (LAYOUT_LABELS = строки × колонки): полоса — все
#: шесть основных метрик в один ряд, сетка — те же шесть в 3×2, подробный —
#: все десять метрик в 2×5 плюс плашка ограничений.
_LAYOUT_SPECS: dict[str, tuple[tuple[str, ...], int]] = {
    "compact": (("temp", "power", "freq", "load", "vram", "vram_used"), 6),
    "grid": (("temp", "power", "freq", "load", "vram", "vram_used"), 2),
    "detailed": (
        (
            "temp",
            "power",
            "freq",
            "load",
            "vram",
            "vram_used",
            "vram_temp",
            "gpu_vr",
            "vram_vr",
            "sa_vr",
        ),
        5,
    ),
}

#: канонический порядок метрик (сетка собирается в независимости от порядка
#: выбора галочек — иначе один и тот же набор рисовался бы по-разному)
CELL_KEYS: tuple[str, ...] = tuple(key for key, _, _, _ in _CELLS)

#: ключ → подпись метрики для чекбоксов вкладки «Оверлей» (единица в скобках)
CELL_CHOICES: dict[str, str] = {key: f"{title} ({unit})" for key, title, unit, _ in _CELLS}


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
        value = float(str(_settings().value("overlay/opacity", DEFAULT_OPACITY)))
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


def _normalize_cells(keys: Iterable[str]) -> tuple[str, ...]:
    """Проверяет набор метрик и приводит его к каноническому порядку.

    Args:
        keys: ключи ячеек в произвольном порядке, возможны дубли.

    Returns:
        Ключи без дублей в каноническом порядке отрисовки.

    Raises:
        ValueError: неизвестный ключ или пустой набор (пустой HUD бессмыслен).
    """
    requested = list(dict.fromkeys(keys))  # дубли схлопываем, порядок пока их
    unknown = [key for key in requested if key not in CELL_CHOICES]
    if unknown:
        raise ValueError(f"неизвестные метрики: {unknown} (ждём {CELL_KEYS})")
    if not requested:
        raise ValueError("нужна хотя бы одна метрика — пустой оверлей бессмыслен")
    chosen = set(requested)
    return tuple(key for key in CELL_KEYS if key in chosen)


def load_overlay_cells() -> tuple[str, ...] | None:
    """Сохранённый набор метрик; None — настройки нет либо там мусор.

    Мусор намеренно НЕ роняет приложение: вызывающий берёт набор из пресета
    текущего макета (см. ``OverlayWindow.__init__``).
    """
    raw = _settings().value("overlay/cells", None)
    if raw is None:
        return None
    parts = [str(item) for item in raw] if isinstance(raw, (list, tuple)) else str(raw).split(",")
    keys = [part.strip() for part in parts if part.strip()]
    if not keys or any(key not in CELL_CHOICES for key in keys):
        return None
    return _normalize_cells(keys)


def save_overlay_cells(keys: Iterable[str]) -> None:
    """Сохраняет набор метрик (валидация — в ``_normalize_cells``)."""
    _settings().setValue("overlay/cells", ",".join(_normalize_cells(keys)))


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
    самодостаточен и не требует открытого главного окна. Любая смена настроек
    эмитит сигнал, чтобы вкладка «Оверлей» и меню «Вид» оставались в согласии —
    ``set_shown``/``set_layout``/``set_opacity``/``set_visible_cells`` являются
    единой точкой истины (правят и настройки, и виджеты).
    """

    layout_changed = Signal(str)  # макет изменён (вкладка/меню/правый клик)
    cells_changed = Signal(object)  # tuple[str, ...] — набор метрик изменён
    opacity_changed = Signal(float)  # непрозрачность изменена
    visibility_changed = Signal(bool)  # показан/спрятан (галочки вкладки и меню)

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
        # Набор метрик: сохранённый (в т.ч. правленный вручную) либо пресет
        # текущего макета — мусор в настройках не должен ломать старт.
        self._visible: tuple[str, ...] = load_overlay_cells() or _LAYOUT_SPECS[self._layout][0]
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
        """Переключает макет: колонки и пресет-набор метрик, затем пересборка.

        Пресет намеренно возвращает свой набор метрик — подписи «Полоса 1×6»
        и т.п. должны соответствовать тому, что реально на экране. Свой набор
        настраивается отдельно — ``set_visible_cells``.

        Args:
            name: Ключ из LAYOUTS.

        Raises:
            ValueError: неизвестный макет.
        """
        if name not in LAYOUTS:
            raise ValueError(f"неизвестный макет: {name!r} (ждём {LAYOUTS})")
        if name == self._layout:
            return
        self._layout = name
        save_overlay_layout(name)
        preset = _LAYOUT_SPECS[name][0]
        cells_changed = preset != self._visible
        if cells_changed:
            self._visible = preset
            save_overlay_cells(preset)
        self._rebuild()
        self.layout_changed.emit(name)
        if cells_changed:
            self.cells_changed.emit(self._visible)

    def visible_cells(self) -> tuple[str, ...]:
        """Какие метрики сейчас показаны (канонический порядок, минимум одна)."""
        return self._visible

    def set_visible_cells(self, keys: Iterable[str]) -> None:
        """Оставляет в HUD только выбранные метрики и пересобирает сетку.

        Args:
            keys: Ключи ячеек (см. CELL_KEYS) в произвольном порядке.

        Raises:
            ValueError: неизвестный ключ либо пустой набор.
        """
        normalized = _normalize_cells(keys)
        if normalized == self._visible:
            return
        self._visible = normalized
        save_overlay_cells(normalized)
        self._rebuild()
        self.cells_changed.emit(normalized)

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

    def set_shown(self, shown: bool) -> None:
        """Показывает/прячет HUD и сохраняет выбор (единая точка истины).

        Так делают все дороги: вкладка «Оверлей», меню «Вид → Оверлей» и
        пункт «Скрыть» в контекстном меню — сигнал ``visibility_changed``
        синхронизирует остальные галочки.
        """
        save_overlay_enabled(shown)
        if shown:
            self.show()
        else:
            self.hide()
        self.visibility_changed.emit(shown)

    def reset_position(self) -> None:
        """Возвращает окно в правый верхний угол основного экрана."""
        self._place_default()
        self._save_geometry()

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
        # Загрузка ядра: % приходит из LoadMeter (ΔactiveTime/Δtimestamp), у
        # него же база — до 2-го тика честное «—», а не ноль.
        self._cells["load"].set_value(snap.gpu_load_percent)
        self._cells["vram"].set_value(snap.vram_eff_freq_mbps, digits=2)
        # Занятость памяти: значение — занято в ГиБ, а сколько это от
        # доступного объёма — в подсказке (на узкой ячейке нет места дроби).
        if snap.vram_used_bytes is None or not snap.vram_total_bytes:
            self._cells["vram_used"].set_value(None)
        else:
            used_gib = snap.vram_used_bytes / 1024**3
            total_gib = snap.vram_total_bytes / 1024**3
            pct = "" if snap.vram_percent is None else f" · {snap.vram_percent:.0f} %"
            self._cells["vram_used"].set_value(
                used_gib, digits=2, tooltip=f"всего {total_gib:.2f} ГиБ{pct}"
            )
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
        """Пересобирает сетку под текущий макет и выбранные метрики.

        Ширина сетки (число колонок) берётся из макета, состав — из
        ``_visible``: выбранные метрики укладываются в ``ceil(n / cols)`` строк.
        """
        while self._grid.count():
            item = self._grid.takeAt(0)  # изымает ячейку, но оставляет её ребёнком
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.hide()
        cols = _LAYOUT_SPECS[self._layout][1]
        keys = self._visible
        for index, key in enumerate(keys):
            cell = self._cells[key]
            self._grid.addWidget(cell, index // cols, index % cols)
            cell.show()
        if self._layout == "detailed":
            self._grid.addWidget(self._limits, -(-len(keys) // cols), 0, 1, cols)
            self._limits.show()
        self.adjustSize()

    def _place_default(self) -> None:
        """Правый верхний угол основного экрана (с отступом 16 px)."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() + 1 - 16, area.top() + 16)

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
        self._place_default()

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

        layout_menu = menu.addMenu(tr("Макет", "Layout"))
        layout_group = QActionGroup(menu)
        layout_group.setExclusive(True)
        for name in LAYOUTS:
            action = QAction(layout_label(name), menu, checkable=True)
            action.setChecked(name == self._layout)
            action.triggered.connect(lambda _=False, n=name: self.set_layout(n))
            layout_group.addAction(action)
            layout_menu.addAction(action)

        opacity_menu = menu.addMenu(tr("Непрозрачность", "Opacity"))
        opacity_group = QActionGroup(menu)
        opacity_group.setExclusive(True)
        for value in OPACITIES:
            action = QAction(f"{value * 100:g} %", menu, checkable=True)
            action.setChecked(abs(value - self._opacity) < 1e-3)
            action.triggered.connect(lambda _=False, v=value: self.set_opacity(v))
            opacity_group.addAction(action)
            opacity_menu.addAction(action)

        menu.addSeparator()
        hide_action = QAction(tr("Скрыть", "Hide"), menu)
        hide_action.triggered.connect(lambda _=False: self.set_shown(False))
        menu.addAction(hide_action)

        menu.exec(event.globalPos())

    def closeEvent(self, event) -> None:  # noqa: N802 (имя Qt API)
        """Запоминает положение перед закрытием (макет/непрозрачность уже сохранены)."""
        self._save_geometry()
        super().closeEvent(event)


__all__ = [
    "CELL_CHOICES",
    "CELL_KEYS",
    "DEFAULT_LAYOUT",
    "DEFAULT_OPACITY",
    "LAYOUTS",
    "LAYOUT_LABELS",
    "LAYOUT_LABELS_EN",
    "OPACITIES",
    "OverlayWindow",
    "layout_label",
    "load_overlay_cells",
    "load_overlay_enabled",
    "load_overlay_layout",
    "load_overlay_opacity",
    "save_overlay_cells",
    "save_overlay_enabled",
    "save_overlay_layout",
    "save_overlay_opacity",
]
