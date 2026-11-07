"""Стиль и фон графиков: пресеты «Классика/Тёмный/Неон/Пульсация».

Единый модуль вида для всех трёх графиков приложения: LiveChart («Мониторинг»
и «Автозгон»), VfChart (кривая V/F) и MiniChart (оверлей HUD). Выбор хранится
в общих настройках ArcTune/ArcTune (``ui/chart_style``) и переключается из
меню «Вид» → «Стиль графиков»; применяется ко всем графикам сразу.

Как устроено (ограничения QtCharts проверены на PySide6 6.12, а не взяты «на
веру»):

- **Свечение (glow)**: у ``QLineSeries`` нет ``setGraphicsEffect`` (и вовсе нет
  ``QGraphicsItem``), поэтому неоновые линии рисуются широкой полупрозрачной
  twin-сериями. Порядок отрисовки QtCharts — последняя добавленная сверху, а
  ``insertSeries`` в PySide6 отсутствует, поэтому twin вставляется ПОД основной
  серией снятием основной и хвоста серий с повторным attachAxis (см.
  ``GlowLayer._add_twin``).
- **Синхронизация точек**: сигнала ``pointsChanged`` у серий нет — точки
  копируются явными вызовами ``GlowLayer.sync`` из мест ``replace``.
  Видимость и pen twin следуют за основной серией.
- **Сброс к классике**: ``setTheme`` с тем же значением НЕ сбрасывает ручные
  overrides кистей/сетки/подписей — нужен форс-тогл (``force_theme``). Тогл не
  трогает ``truncateLabels`` и диапазоны осей (иначе вернулся бы старый баг с
  «…»), но перекрашивает pen'ы серий — вызывающий восстанавливает их сам.
- **Пульсация**: ``QTimer`` 100 мс, фаза от ``time.monotonic`` (период 2 с),
  тик пропускается, пока график не виден — фон «дышит» только на экране.

Read-only по отношению к карте: модуль меняет исключительно рисование.
"""

from __future__ import annotations

import contextlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from PySide6.QtCharts import QChart, QChartView, QLineSeries
from PySide6.QtCore import QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QPen

from arctuner.i18n import tr

#: Известные стили (порядок = порядок пунктов меню).
CHART_STYLES: Final[tuple[str, ...]] = ("classic", "dark", "neon", "pulse")
DEFAULT_CHART_STYLE: Final = "classic"

_GLOW_ALPHA: Final = 60  # непрозрачность подложки свечения (из 255)
_GLOW_WIDTH_FACTOR: Final = 3  # подложка шире линии во столько раз
_PULSE_INTERVAL_MS: Final = 100
_PULSE_PERIOD_S: Final = 2.0


@dataclass(frozen=True)
class ChartStyle:
    """Спецификация одного стиля графиков (только визуал, без данных)."""

    name: str
    bg: str  # фон графика (chart background)
    plot_bg: str  # фон области построения
    grid: str  # цвет сетки и линий осей
    labels: str  # цвет подписей осей и легенды
    line_width: float  # ширина линий серий
    glow: bool  # светящиеся twin-подложки
    pulse: bool  # пульсирующая подсветка фона
    pulse_hi: str  # цвет «верхней» точки пульсации


_STYLES: Final[dict[str, ChartStyle]] = {
    # Классика — текущий «приборный» вид; цвета ниже не применяются напрямую:
    # стиль сбрасывает графики к дефолтной светлой теме QChart (см. force_theme).
    "classic": ChartStyle(
        name="classic",
        bg="#ffffff",
        plot_bg="#ffffff",
        grid="#e2e2e2",
        labels="#404044",
        line_width=2.0,
        glow=False,
        pulse=False,
        pulse_hi="#ffffff",
    ),
    "dark": ChartStyle(
        name="dark",
        bg="#232629",
        plot_bg="#2d3035",
        grid="#43474e",
        labels="#e8e8e8",
        line_width=2.0,
        glow=False,
        pulse=False,
        pulse_hi="#ffffff",
    ),
    "neon": ChartStyle(
        name="neon",
        bg="#05070d",
        plot_bg="#0a0e18",
        grid="#1f3b55",
        labels="#9fe0ff",
        line_width=3.0,
        glow=True,
        pulse=False,
        pulse_hi="#16233d",
    ),
    "pulse": ChartStyle(
        name="pulse",
        bg="#05070d",
        plot_bg="#0a0e18",
        grid="#1f3b55",
        labels="#9fe0ff",
        line_width=3.0,
        glow=True,
        pulse=True,
        pulse_hi="#16233d",
    ),
}


def chart_style(name: str) -> ChartStyle:
    """Спецификация стиля; неизвестное имя — ValueError (как save_theme)."""
    try:
        return _STYLES[name]
    except KeyError:
        raise ValueError(f"неизвестный стиль графиков: {name!r} (ждём {CHART_STYLES})") from None


def load_chart_style() -> str:
    """Стиль из настроек; мусор/отсутствие принимается за дефолт."""
    value = QSettings("ArcTune", "ArcTune").value("ui/chart_style", DEFAULT_CHART_STYLE)
    return value if value in CHART_STYLES else DEFAULT_CHART_STYLE


def save_chart_style(name: str) -> None:
    """Сохраняет стиль; применяется немедленно вызывающим (окно)."""
    if name not in CHART_STYLES:
        raise ValueError(f"неизвестный стиль графиков: {name!r} (ждём {CHART_STYLES})")
    QSettings("ArcTune", "ArcTune").setValue("ui/chart_style", name)


def chart_style_label(name: str) -> str:
    """Подпись стиля для меню на текущем языке."""
    labels: dict[str, tuple[str, str]] = {
        "classic": ("Классика", "Classic"),
        "dark": ("Тёмный", "Dark"),
        "neon": ("Неон", "Neon"),
        "pulse": ("Пульсация", "Pulse"),
    }
    if name not in labels:
        raise ValueError(f"неизвестный стиль графиков: {name!r} (ждём {CHART_STYLES})")
    ru, en = labels[name]
    return tr(ru, en)


def force_theme(chart: QChart, dark: bool) -> None:
    """Сбрасывает ручные overrides темы QChart форс-тоглом.

    ``setTheme`` с тем же значением ничего не сбрасывает (проверено), а смена
    темы сбрасывает кисти фона, plot area, сетку и подписи к дефолтам темы —
    поэтому при совпадении темы сначала уходим на противоположную.
    Побочный эффект: pen'ы серий перекрашиваются темой — вызывающий обязан
    восстановить их после вызова. ``truncateLabels`` и диапазоны осей тогл не
    трогает.
    """
    target = QChart.ChartTheme.ChartThemeDark if dark else QChart.ChartTheme.ChartThemeLight
    opposite = QChart.ChartTheme.ChartThemeLight if dark else QChart.ChartTheme.ChartThemeDark
    if chart.theme() == target:
        chart.setTheme(opposite)
    chart.setTheme(target)


def apply_chart_style(chart: QChart, spec: ChartStyle) -> None:
    """Накладывает кисти/сетку/подписи стиля (кроме классики — см. force_theme).

    Pen'ы серий не трогаются: ширина линий задаётся вызывающим графиком,
    цвета остаются каноническими (единый источник — theme.SERIES_*).
    """
    chart.setBackgroundBrush(QColor(spec.bg))
    chart.setPlotAreaBackgroundBrush(QColor(spec.plot_bg))
    chart.setPlotAreaBackgroundVisible(True)
    grid_pen = QPen(QColor(spec.grid), 1)
    axis_line_pen = QPen(QColor(spec.grid), 1)
    labels = QColor(spec.labels)
    for axis in chart.axes():
        axis.setGridLinePen(grid_pen)
        axis.setLinePen(axis_line_pen)
        axis.setLabelsColor(labels)
        axis.setTitleBrush(labels)
    chart.legend().setLabelColor(labels)


class GlowLayer:
    """Светящиеся twin-подложки под линиями одного графика.

    Серии регистрируются через :meth:`track` (всегда, даже выключенным
    стилем — чтобы включение не потеряло лениво созданные серии), а
    :meth:`set_active` создаёт/удаляет подложки и переносит их под основные
    линии. Точки копируются явным :meth:`sync` — сигнала ``pointsChanged`` у
    QtCharts нет.
    """

    def __init__(self, chart: QChart) -> None:
        self._chart = chart
        self._tracked: list[QLineSeries] = []
        self._twins: dict[QLineSeries, QLineSeries] = {}
        self._visible_handlers: dict[QLineSeries, Callable[[], None]] = {}
        self._active = False

    @property
    def active(self) -> bool:
        """Свечение сейчас включено?"""
        return self._active

    @property
    def twins(self) -> dict[QLineSeries, QLineSeries]:
        """Основные серии и их подложки (для тестов/отладки)."""
        return dict(self._twins)

    @property
    def tracked(self) -> tuple[QLineSeries, ...]:
        """Зарегистрированные основные серии (для наложения ширины линий)."""
        return tuple(self._tracked)

    def track(self, main: QLineSeries) -> None:
        """Регистрирует основную серию (series уже добавлен в chart)."""
        if main in self._tracked:
            return
        self._tracked.append(main)

        # pointsChanged у QtCharts нет, а visibleChanged/penChanged есть —
        # видимость подложки следует за основной серией автоматически.
        def _on_visible(m: QLineSeries = main) -> None:
            self.sync(m)

        main.visibleChanged.connect(_on_visible)
        self._visible_handlers[main] = _on_visible
        if self._active:
            self._add_twin(main)

    def untrack(self, main: QLineSeries) -> None:
        """Забывает серию, гасит подписку и удаляет её подложку."""
        handler = self._visible_handlers.pop(main, None)
        if handler is not None:
            # sender уже уничтожен Qt — disconnect бросает RuntimeError
            with contextlib.suppress(RuntimeError, TypeError):
                main.visibleChanged.disconnect(handler)
        self._drop_twin(main)
        if main in self._tracked:
            self._tracked.remove(main)

    def set_active(self, active: bool) -> None:
        """Включает/выключает свечение; при включении подложки пересоздаются.

        Пересоздание нужно и при неизменном флаге: pen/ширина основных серий
        могли измениться вместе со стилем.
        """
        for twin in self._twins.values():
            self._chart.removeSeries(twin)
            twin.deleteLater()
        self._twins.clear()
        self._active = active
        if active:
            for main in list(self._tracked):
                if main in self._chart.series():
                    self._add_twin(main)

    def sync(self, main: QLineSeries) -> None:
        """Копирует точки и видимость основной серии в её подложку."""
        twin = self._twins.get(main)
        if twin is None:
            return
        twin.replace(main.points())
        twin.setVisible(main.isVisible())

    def sync_all(self) -> None:
        """Синхронизирует все активные подложки (после clear и т.п.)."""
        for main in self._twins:
            self.sync(main)

    # --- внутреннее ---

    def _add_twin(self, main: QLineSeries) -> None:
        """Ставит подложку непосредственно ПОД основной серией.

        QtCharts рисует серии в порядке добавления (последняя сверху), а
        insertSeries в PySide6 нет — поэтому снимаем основную серию и всё, что
        после неё, добавляем подложку и возвращаем снятые серии обратно с
        повторным attachAxis (после removeSeries оси отвязываются).
        """
        chart = self._chart
        horizontal = chart.axes(Qt.Orientation.Horizontal, main)
        vertical = chart.axes(Qt.Orientation.Vertical, main)
        series = chart.series()
        if main not in series:
            return
        index = series.index(main)
        moved = list(series[index:])  # основная серия + хвост
        saved_axes = {
            s: chart.axes(Qt.Orientation.Horizontal, s) + chart.axes(Qt.Orientation.Vertical, s)
            for s in moved
        }
        for s in moved:
            chart.removeSeries(s)

        pen = main.pen()
        glow_color = QColor(pen.color())
        glow_color.setAlpha(_GLOW_ALPHA)
        twin = QLineSeries()
        twin.setName("")
        twin.setPen(QPen(glow_color, max(pen.widthF(), 1.0) * _GLOW_WIDTH_FACTOR))
        chart.addSeries(twin)
        for s in moved:
            chart.addSeries(s)
        for s in moved:
            for axis in saved_axes[s]:
                s.attachAxis(axis)
        for axis in horizontal + vertical:
            twin.attachAxis(axis)
        twin.replace(main.points())
        twin.setVisible(main.isVisible())
        for marker in chart.legend().markers(twin):
            marker.setVisible(False)
        # remove+add сбрасывает скрытые вручную маркеры легенды у безымянных
        # серий (доп. сегменты MiniChart, маркеры событий) — возвращаем скрытие.
        for s in moved:
            if not s.name():
                for marker in chart.legend().markers(s):
                    marker.setVisible(False)
        self._twins[main] = twin

    def _drop_twin(self, main: QLineSeries) -> None:
        twin = self._twins.pop(main, None)
        if twin is None:
            return
        self._chart.removeSeries(twin)
        twin.deleteLater()


class PulseController(QObject):
    """Пульсирующая подсветка фона графика (стиль «Пульсация»).

    Фон plot area «дышит» синусоидой между ``plot_bg`` и ``pulse_hi``
    (период 2 с, шаг таймера 100 мс). Фаза берётся от ``time.monotonic``, а
    не от счётчика тиков — анимация непрерывна при паузах, и тесты могут
    подменять часы детерминированно через атрибут ``_clock``.
    """

    def __init__(self, view: QChartView) -> None:
        super().__init__(view)
        self._view = view
        self._timer = QTimer(self)
        self._timer.setInterval(_PULSE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._base = QColor()
        self._high = QColor()
        self._active = False
        self._clock = time.monotonic

    @property
    def active(self) -> bool:
        """Анимация сейчас запущена?"""
        return self._active

    @property
    def running(self) -> bool:
        """Таймер тикает?"""
        return self._timer.isActive()

    def set_style(self, spec: ChartStyle) -> None:
        """Включает пульсацию для стиля (spec.pulse) или гасит её."""
        if not spec.pulse:
            self.stop()
            return
        self._base = QColor(spec.plot_bg)
        self._high = QColor(spec.pulse_hi)
        self._active = True
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Останавливает анимацию (фон уже накладывает apply_chart_style)."""
        self._active = False
        self._timer.stop()

    def _tick(self) -> None:
        if not self._active or not self._view.isVisible():
            return
        phase = (
            math.sin((self._clock() % _PULSE_PERIOD_S) / _PULSE_PERIOD_S * math.tau) + 1.0
        ) / 2.0
        chart = self._view.chart()
        chart.setPlotAreaBackgroundBrush(_blend(self._base, self._high, phase))


def _blend(low: QColor, high: QColor, t: float) -> QColor:
    """Линейная интерполяция двух цветов (t: 0 — low, 1 — high)."""
    return QColor(
        round(low.red() + (high.red() - low.red()) * t),
        round(low.green() + (high.green() - low.green()) * t),
        round(low.blue() + (high.blue() - low.blue()) * t),
    )


__all__ = [
    "CHART_STYLES",
    "DEFAULT_CHART_STYLE",
    "ChartStyle",
    "GlowLayer",
    "PulseController",
    "apply_chart_style",
    "chart_style",
    "chart_style_label",
    "force_theme",
    "load_chart_style",
    "save_chart_style",
]
