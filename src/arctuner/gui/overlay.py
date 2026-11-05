"""Оверлей ArcTune: компактный HUD поверх окон (read-only).

Мини-панель с живой телеметрией, всегда поверх других окон — видна поверх
игр и бенчмарков в оконном/безрамочном режиме. Ограничение ОС, а не проекта:
в эксклюзивном fullscreen Windows не показывает поверх оверлеи сторонних
процессов.

Выбор пользователя сохраняется в настройках (ArcTune/ArcTune — тот же файл,
что масштаб и тема):

* ``overlay/enabled``   — включён ли оверлей (вкладка «Оверлей», меню «Вид»);
* ``overlay/layout``    — макет: полоса 1×6, сетка 3×2, подробный 2×5;
* ``overlay/orientation`` — горизонтальное (по умолчанию) или вертикальное размещение;
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

import time
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

from arctuner.cpu import CpuSnapshot
from arctuner.gui.minichart import (
    DEFAULT_MINI_CHART_SERIES,
    DEFAULT_MINI_CHART_SIZE,
    DEFAULT_MINI_CHART_WINDOW,
    MINI_CHART_SERIES,
    MINI_CHART_SIZES,
    MINI_CHART_WINDOWS,
    MiniChart,
    normalize_mini_chart_series,
)
from arctuner.i18n import tr
from arctuner.igcl.api import TelemetrySnapshot

#: макеты HUD (выбор пользователя; хранится в настройках)
LAYOUTS: tuple[str, ...] = ("compact", "grid", "detailed")
ORIENTATIONS: tuple[str, ...] = ("horizontal", "vertical")

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

CELL_LABELS_EN: dict[str, str] = {
    "temp": "Temperature",
    "power": "Power",
    "freq": "Core",
    "load": "GPU Load",
    "vram": "Memory Speed",
    "vram_used": "VRAM Used",
    "vram_temp": "Memory Temp",
    "gpu_vr": "Core VRM",
    "vram_vr": "Memory VRM",
    "sa_vr": "SA VRM",
    "cpu_load": "CPU Load",
    "cpu_freq": "CPU Frequency",
    "cpu_temp": "CPU Temperature",
}


def layout_label(name: str) -> str:
    """Подпись макета на текущем языке интерфейса (см. arctuner.i18n)."""
    return tr(LAYOUT_LABELS[name], LAYOUT_LABELS_EN[name])


def orientation_label(name: str) -> str:
    """Подпись ориентации HUD для текущего языка интерфейса."""
    labels = {
        "horizontal": ("Горизонтально", "Horizontal"),
        "vertical": ("Вертикально", "Vertical"),
    }
    ru, en = labels[name]
    return tr(ru, en)


def cell_label(key: str) -> str:
    """Подпись метрики для текущего языка интерфейса."""
    cells = {cell_key: (title, unit) for cell_key, title, unit, _ in _CELLS}
    title, unit = cells[key]
    units_en = {"°C": "°C", "Вт": "W", "МГц": "MHz", "%": "%", "Гбит/с": "Gbps", "ГиБ": "GiB"}
    return f"{tr(title, CELL_LABELS_EN[key])} ({tr(unit, units_en[unit])})"


#: допустимая непрозрачность (доля от 1.0)
OPACITIES: tuple[float, ...] = (0.5, 0.7, 0.85, 1.0)

DEFAULT_LAYOUT: str = "compact"
DEFAULT_ORIENTATION: str = "horizontal"
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
    ("cpu_load", "CPU загрузка", "%", "load"),
    ("cpu_freq", "CPU частота", "МГц", "freq"),
    ("cpu_temp", "CPU температура", "°C", "temp"),
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


def load_mini_chart_enabled() -> bool:
    """Мини-график включён ли пользователем (по умолчанию — выключен)."""
    return _as_bool(_settings().value("overlay/chart_enabled"), default=False)


def save_mini_chart_enabled(value: bool) -> None:
    """Сохраняет выбор показа мини-графика."""
    _settings().setValue("overlay/chart_enabled", bool(value))


def load_mini_chart_series() -> tuple[str, ...]:
    """Серии мини-графика; повреждённое значение заменяется набором по умолчанию."""
    raw = _settings().value("overlay/chart_series")
    if raw is None:
        return DEFAULT_MINI_CHART_SERIES
    try:
        return normalize_mini_chart_series(str(raw).split(","))
    except ValueError:
        return DEFAULT_MINI_CHART_SERIES


def save_mini_chart_series(keys: Iterable[str]) -> None:
    """Проверяет и сохраняет набор серий мини-графика."""
    normalized = normalize_mini_chart_series(list(keys))
    _settings().setValue("overlay/chart_series", ",".join(normalized))


def load_mini_chart_window() -> int:
    """Временное окно графика; мусор схлопывается к безопасному дефолту."""
    raw = _settings().value("overlay/chart_window", DEFAULT_MINI_CHART_WINDOW)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return DEFAULT_MINI_CHART_WINDOW
    return value if value in MINI_CHART_WINDOWS else DEFAULT_MINI_CHART_WINDOW


def save_mini_chart_window(window_s: int) -> None:
    """Проверяет и сохраняет длину временного окна мини-графика."""
    if window_s not in MINI_CHART_WINDOWS:
        raise ValueError(f"временное окно мини-графика вне {MINI_CHART_WINDOWS}: {window_s}")
    _settings().setValue("overlay/chart_window", window_s)


def load_mini_chart_size() -> str:
    """Размер графика; мусор схлопывается к компактному."""
    raw = _settings().value("overlay/chart_size", DEFAULT_MINI_CHART_SIZE)
    return raw if raw in MINI_CHART_SIZES else DEFAULT_MINI_CHART_SIZE


def save_mini_chart_size(size: str) -> None:
    """Проверяет и сохраняет размер мини-графика."""
    if size not in MINI_CHART_SIZES:
        raise ValueError(f"неизвестный размер мини-графика: {size!r}")
    _settings().setValue("overlay/chart_size", size)


def load_overlay_layout() -> str:
    """Макет из настроек; мусор/отсутствие принимается за дефолт (полоса)."""
    value = _settings().value("overlay/layout", DEFAULT_LAYOUT)
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def save_overlay_layout(name: str) -> None:
    """Сохраняет макет; применяется немедленно ``OverlayWindow.set_layout``."""
    if name not in LAYOUTS:
        raise ValueError(f"неизвестный макет: {name!r} (ждём {LAYOUTS})")
    _settings().setValue("overlay/layout", name)


def load_overlay_orientation() -> str:
    """Ориентация ячеек; по умолчанию HUD расположен горизонтально."""
    value = _settings().value("overlay/orientation", DEFAULT_ORIENTATION)
    return value if value in ORIENTATIONS else DEFAULT_ORIENTATION


def save_overlay_orientation(orientation: str) -> None:
    """Проверяет и сохраняет ориентацию ячеек HUD."""
    if orientation not in ORIENTATIONS:
        raise ValueError(f"неизвестная ориентация оверлея: {orientation!r}")
    _settings().setValue("overlay/orientation", orientation)


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
    orientation_changed = Signal(str)
    cells_changed = Signal(object)  # tuple[str, ...] — набор метрик изменён
    opacity_changed = Signal(float)  # непрозрачность изменена
    visibility_changed = Signal(bool)  # показан/спрятан (галочки вкладки и меню)
    chart_enabled_changed = Signal(bool)
    chart_series_changed = Signal(object)
    chart_window_changed = Signal(int)
    chart_size_changed = Signal(str)
    chart_paused_changed = Signal(bool)

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
        self.setWindowTitle(tr("ArcTune — оверлей", "ArcTune — overlay"))

        self._layout = load_overlay_layout()
        self._orientation = load_overlay_orientation()
        # Набор метрик: сохранённый (в т.ч. правленный вручную) либо пресет
        # текущего макета — мусор в настройках не должен ломать старт.
        self._visible: tuple[str, ...] = load_overlay_cells() or _LAYOUT_SPECS[self._layout][0]
        self._opacity = load_overlay_opacity()
        self.setWindowOpacity(self._opacity)
        self._drag_offset: QPoint | None = None

        self._cells: dict[str, _Cell] = {}
        for key, title, unit, accent in _CELLS:
            unit_en = {
                "°C": "°C",
                "Вт": "W",
                "МГц": "MHz",
                "%": "%",
                "Гбит/с": "Gbps",
                "ГиБ": "GiB",
            }[unit]
            self._cells[key] = _Cell(tr(title, CELL_LABELS_EN[key]), tr(unit, unit_en), accent)

        self._limits = QLabel("")
        self._limits.setObjectName("overlayLimits")
        self._limits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._limits.setProperty("active", "false")
        self._limits.setToolTip(
            tr(
                "Активный лимитер прямо сейчас: Power / Temperature / Current / Voltage / "
                "Utilization Limited. «нет (запас есть)» — ничего не ограничивает.",
                "Active limiter right now: Power / Temperature / Current / Voltage / "
                "Utilization Limited. “None (headroom available)” means no active limit.",
            )
        )
        self._chart_enabled = load_mini_chart_enabled()
        self._chart_paused = False
        self._chart_t0 = time.monotonic()
        self._active_limits: set[str] | None = None
        self._mini_chart = MiniChart(load_mini_chart_window())
        self._mini_chart.set_series(load_mini_chart_series())
        self._mini_chart.set_size(load_mini_chart_size())
        self._mini_chart.setVisible(self._chart_enabled)

        root = QFrame(self)
        root.setObjectName("overlayRoot")
        self._grid = QGridLayout()
        self._grid.setSpacing(4)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(6, 5, 6, 5)
        root_lay.setSpacing(4)
        root_lay.addLayout(self._grid)
        root_lay.addWidget(self._mini_chart)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(root)

        self._rebuild()
        self._restore_geometry()

    # --- настройки ---

    def layout_name(self) -> str:
        """Текущий макет (см. LAYOUTS)."""
        return self._layout

    def orientation(self) -> str:
        """Текущая ориентация ячеек HUD."""
        return self._orientation

    def set_orientation(self, orientation: str) -> None:
        """Располагает ячейки горизонтально или вертикально."""
        if orientation not in ORIENTATIONS:
            raise ValueError(f"неизвестная ориентация оверлея: {orientation!r}")
        if orientation == self._orientation:
            return
        save_overlay_orientation(orientation)
        self._orientation = orientation
        self._rebuild()
        self.orientation_changed.emit(orientation)

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

    def mini_chart_enabled(self) -> bool:
        """Возвращает состояние показа мини-графика."""
        return self._chart_enabled

    def mini_chart_series(self) -> tuple[str, ...]:
        """Возвращает выбранные серии мини-графика."""
        return self._mini_chart.selected_series

    def mini_chart_window(self) -> int:
        """Возвращает длительность окна мини-графика в секундах."""
        return self._mini_chart.window_s

    def mini_chart_size(self) -> str:
        """Возвращает выбранный размер графика."""
        return self._mini_chart.size_name

    def set_mini_chart_enabled(self, enabled: bool) -> None:
        """Включает или скрывает мини-график, сохраняя настройку."""
        enabled = bool(enabled)
        if enabled == self._chart_enabled:
            return
        self._chart_enabled = enabled
        save_mini_chart_enabled(enabled)
        self._mini_chart.setVisible(enabled)
        self.adjustSize()
        self.chart_enabled_changed.emit(enabled)

    def set_mini_chart_series(self, keys: Iterable[str]) -> None:
        """Выбирает серии графика, сохраняя их в пользовательских настройках."""
        normalized = normalize_mini_chart_series(list(keys))
        if normalized == self._mini_chart.selected_series:
            return
        save_mini_chart_series(normalized)
        self._mini_chart.set_series(normalized)
        self.chart_series_changed.emit(normalized)

    def set_mini_chart_window(self, window_s: int) -> None:
        """Меняет видимый временной диапазон графика."""
        if window_s == self._mini_chart.window_s:
            return
        save_mini_chart_window(window_s)
        self._mini_chart.set_window(window_s)
        self.chart_window_changed.emit(window_s)

    def set_mini_chart_size(self, size: str) -> None:
        """Меняет размер графика и сохраняет его."""
        if size == self._mini_chart.size_name:
            return
        save_mini_chart_size(size)
        self._mini_chart.set_size(size)
        self.adjustSize()
        self.chart_size_changed.emit(size)

    def set_mini_chart_paused(self, paused: bool) -> None:
        """Приостанавливает только визуализацию, не опрос телеметрии."""
        paused = bool(paused)
        if paused == self._chart_paused:
            return
        self._chart_paused = paused
        self.chart_paused_changed.emit(paused)

    def clear_mini_chart(self) -> None:
        """Очищает видимую историю графика."""
        self._mini_chart.clear()

    @Slot(str, str)
    def on_watchdog_event(self, event_type: str, detail: str) -> None:
        """Добавляет событие watchdog на шкалу мини-графика."""
        if self._chart_enabled:
            label = f"{event_type}: {detail}"
            self._mini_chart.add_event_marker(time.monotonic() - self._chart_t0, label, "watchdog")

    @Slot(str)
    def on_setting_applied(self, detail: str) -> None:
        """Добавляет подтверждённое ручное изменение на шкалу мини-графика."""
        if self._chart_enabled:
            self._mini_chart.add_event_marker(
                time.monotonic() - self._chart_t0,
                detail,
                "setting",
            )

    def reset_position(self) -> None:
        """Возвращает окно в правый верхний угол основного экрана."""
        self._place_default()
        self._save_geometry()

    # --- данные ---

    @Slot(object)
    def on_telemetry(self, snap: TelemetrySnapshot) -> None:
        """Обновляет ячейки по новому снимку (слот воркера: 1 Гц, ~2 Гц в поиске)."""
        limit_names = {
            "power_limited": tr("Мощность", "Power"),
            "temperature_limited": tr("Температура", "Temperature"),
            "current_limited": tr("Ток", "Current"),
            "voltage_limited": tr("Напряжение", "Voltage"),
            "utilization_limited": tr("Загрузка", "Utilization"),
        }
        active_limits = {name for name in limit_names if getattr(snap, name)}
        started = active_limits - self._active_limits if self._active_limits is not None else set()
        if started and self._chart_enabled:
            labels = ", ".join(limit_names[name] for name in sorted(started))
            self._mini_chart.add_event_marker(
                time.monotonic() - self._chart_t0,
                tr(f"Лимит: {labels}", f"Limit: {labels}"),
                "limiter",
            )
        self._active_limits = active_limits
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
        if self._chart_enabled and not self._chart_paused:
            self._mini_chart.push(time.monotonic() - self._chart_t0, snap)

    @Slot(object)
    def on_cpu(self, snapshot: CpuSnapshot) -> None:
        """Обновляет выбранные CPU-ячейки; источник CPU независим от IGCL."""
        if not self.isVisible():
            return
        per_core = ", ".join(f"{value:.0f} %" for value in snapshot.per_core_usage_percent)
        self._cells["cpu_load"].set_value(
            snapshot.usage_percent,
            tooltip=tr(f"По ядрам: {per_core}", f"Per core: {per_core}") if per_core else "",
        )
        self._cells["cpu_freq"].set_value(snapshot.frequency_mhz)
        self._cells["cpu_temp"].set_value(snapshot.temperature_c)

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
        summary = snap.limit_summary()
        if summary == "нет (запас есть)":
            summary = tr(summary, "None (headroom available)")
        self._limits.setText(f"{tr('Лимит', 'Limit')}: {summary}")
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
        cols = _LAYOUT_SPECS[self._layout][1] if self._orientation == "horizontal" else 1
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

        orientation_menu = menu.addMenu(tr("Ориентация", "Orientation"))
        orientation_group = QActionGroup(menu)
        orientation_group.setExclusive(True)
        for orientation in ORIENTATIONS:
            action = QAction(orientation_label(orientation), menu, checkable=True)
            action.setChecked(orientation == self._orientation)
            action.triggered.connect(lambda _=False, value=orientation: self.set_orientation(value))
            orientation_group.addAction(action)
            orientation_menu.addAction(action)

        opacity_menu = menu.addMenu(tr("Непрозрачность", "Opacity"))
        opacity_group = QActionGroup(menu)
        opacity_group.setExclusive(True)
        for value in OPACITIES:
            action = QAction(f"{value * 100:g} %", menu, checkable=True)
            action.setChecked(abs(value - self._opacity) < 1e-3)
            action.triggered.connect(lambda _=False, v=value: self.set_opacity(v))
            opacity_group.addAction(action)
            opacity_menu.addAction(action)

        chart_menu = menu.addMenu(tr("Мини-график", "Mini chart"))
        chart_enabled = QAction(tr("Показывать график", "Show chart"), menu, checkable=True)
        chart_enabled.setChecked(self._chart_enabled)
        chart_enabled.triggered.connect(self.set_mini_chart_enabled)
        chart_menu.addAction(chart_enabled)
        chart_paused = QAction(tr("Пауза", "Pause"), menu, checkable=True)
        chart_paused.setChecked(self._chart_paused)
        chart_paused.setEnabled(self._chart_enabled)
        chart_paused.triggered.connect(self.set_mini_chart_paused)
        chart_menu.addAction(chart_paused)
        clear_chart = QAction(tr("Очистить график", "Clear chart"), menu)
        clear_chart.setEnabled(self._chart_enabled)
        clear_chart.triggered.connect(self.clear_mini_chart)
        chart_menu.addAction(clear_chart)
        size_menu = chart_menu.addMenu(tr("Размер", "Size"))
        size_group = QActionGroup(menu)
        size_group.setExclusive(True)
        for size in MINI_CHART_SIZES:
            label = tr("Компактный", "Compact") if size == "compact" else tr("Широкий", "Wide")
            action = QAction(label, menu, checkable=True)
            action.setChecked(size == self._mini_chart.size_name)
            action.triggered.connect(lambda _=False, value=size: self.set_mini_chart_size(value))
            size_group.addAction(action)
            size_menu.addAction(action)

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
    "DEFAULT_MINI_CHART_SERIES",
    "DEFAULT_MINI_CHART_SIZE",
    "DEFAULT_MINI_CHART_WINDOW",
    "DEFAULT_OPACITY",
    "DEFAULT_ORIENTATION",
    "LAYOUTS",
    "LAYOUT_LABELS",
    "LAYOUT_LABELS_EN",
    "MINI_CHART_SERIES",
    "MINI_CHART_SIZES",
    "MINI_CHART_WINDOWS",
    "OPACITIES",
    "ORIENTATIONS",
    "OverlayWindow",
    "layout_label",
    "load_mini_chart_enabled",
    "load_mini_chart_series",
    "load_mini_chart_size",
    "load_mini_chart_window",
    "load_overlay_cells",
    "load_overlay_enabled",
    "load_overlay_layout",
    "load_overlay_opacity",
    "load_overlay_orientation",
    "orientation_label",
    "save_mini_chart_enabled",
    "save_mini_chart_series",
    "save_mini_chart_size",
    "save_mini_chart_window",
    "save_overlay_cells",
    "save_overlay_enabled",
    "save_overlay_layout",
    "save_overlay_opacity",
    "save_overlay_orientation",
]
