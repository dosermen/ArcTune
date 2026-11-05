"""Компактный read-only график телеметрии для HUD."""

from __future__ import annotations

from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QToolTip

from arctuner.gui.theme import SERIES_FREQ, SERIES_LOAD, SERIES_POWER, SERIES_TEMP, SERIES_VRAM
from arctuner.i18n import tr
from arctuner.igcl.api import TelemetrySnapshot

MINI_CHART_SERIES: tuple[str, ...] = ("temp", "power", "load", "vram", "freq")
MINI_CHART_WINDOWS: tuple[int, ...] = (30, 60, 120)
MINI_CHART_SIZES: tuple[str, ...] = ("compact", "wide")
DEFAULT_MINI_CHART_SERIES: tuple[str, ...] = ("temp", "power", "load", "freq")
DEFAULT_MINI_CHART_WINDOW = 60
DEFAULT_MINI_CHART_SIZE = "compact"
_SIZE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "compact": (360, 110),
    "wide": (560, 160),
}
_GAP_MAX_S = 5.0
_MAX_SAMPLES = max(MINI_CHART_WINDOWS) * 2
_MAX_EVENTS = 100
_EVENT_COLORS = {"watchdog": QColor("#ef5350"), "limiter": QColor("#ffb74d")}

_METRICS: dict[str, tuple[str, str, QColor, bool]] = {
    "temp": ("Temperature, °C", "Температура, °C", QColor(SERIES_TEMP), False),
    "power": ("Power, %", "Мощность, %", QColor(SERIES_POWER), False),
    "load": ("GPU Load, %", "Загрузка, %", QColor(SERIES_LOAD), False),
    "vram": ("VRAM Used, %", "Память, %", QColor(SERIES_VRAM), False),
    "freq": ("Frequency, MHz", "Частота, МГц", QColor(SERIES_FREQ), True),
}


def normalize_mini_chart_series(keys: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    selected = set(keys)
    unknown = selected - set(MINI_CHART_SERIES)
    if unknown:
        raise ValueError(f"неизвестные серии мини-графика: {sorted(unknown)}")
    if not selected:
        raise ValueError("выберите хотя бы одну серию мини-графика")
    return tuple(key for key in MINI_CHART_SERIES if key in selected)


class MiniChart(QChartView):
    """Мини-график с отдельными осями и разрывами при пропуске телеметрии."""

    def __init__(self, window_s: int, parent=None) -> None:
        super().__init__(parent)
        if window_s not in MINI_CHART_WINDOWS:
            raise ValueError(f"временное окно мини-графика вне {MINI_CHART_WINDOWS}: {window_s}")
        self._window_s = window_s
        self._size = DEFAULT_MINI_CHART_SIZE
        self._selected = DEFAULT_MINI_CHART_SERIES
        self._samples: deque[tuple[float, dict[str, float | None]]] = deque(maxlen=_MAX_SAMPLES)
        self._events: deque[tuple[float, str, str]] = deque(maxlen=_MAX_EVENTS)
        self._series: dict[str, list[QLineSeries]] = {key: [] for key in MINI_CHART_SERIES}
        self._event_series: list[QLineSeries] = []
        self._last_t: float | None = None
        self._inspector_text = ""

        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0.0, float(window_s))
        self._axis_x.setTitleText(tr("сек", "s"))
        self._axis_left = QValueAxis()
        self._axis_left.setRange(0.0, 100.0)
        self._axis_left.setTitleText("°C / %")
        self._axis_right = QValueAxis()
        self._axis_right.setRange(0.0, 3300.0)
        self._axis_right.setTitleText(tr("МГц", "MHz"))
        for axis in (self._axis_x, self._axis_left, self._axis_right):
            axis.setTruncateLabels(False)
        chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._axis_left, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(self._axis_right, Qt.AlignmentFlag.AlignRight)
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.set_size(self._size)
        self.setVisible(False)
        self.set_series(self._selected)

    @property
    def window_s(self) -> int:
        """Длительность видимого временного окна."""
        return self._window_s

    @property
    def selected_series(self) -> tuple[str, ...]:
        """Выбранные серии в каноническом порядке."""
        return self._selected

    @property
    def size_name(self) -> str:
        """Размер мини-графика."""
        return self._size

    def set_size(self, size: str) -> None:
        """Задаёт компактный или широкий размер графика."""
        if size not in MINI_CHART_SIZES:
            raise ValueError(f"неизвестный размер мини-графика: {size!r}")
        self._size = size
        width, height = _SIZE_DIMENSIONS[size]
        self.setFixedSize(width, height)

    def set_dark(self, dark: bool) -> None:
        """Сохраняет приборный вид графика одинаковым при смене темы."""
        del dark

    def set_series(self, keys: tuple[str, ...] | list[str]) -> None:
        """Задаёт видимые метрики."""
        self._selected = normalize_mini_chart_series(keys)
        self._render()

    def set_window(self, window_s: int) -> None:
        """Меняет временное окно, не сбрасывая накопленные точки."""
        if window_s not in MINI_CHART_WINDOWS:
            raise ValueError(f"временное окно мини-графика вне {MINI_CHART_WINDOWS}: {window_s}")
        if window_s == self._window_s:
            return
        self._window_s = window_s
        self._render()

    def clear(self) -> None:
        """Очищает историю точек и событий."""
        self._samples.clear()
        self._events.clear()
        self._last_t = None
        self._render()

    def add_event_marker(self, t: float, label: str, kind: str) -> None:
        """Добавляет watchdog- или limiter-событие на временную шкалу."""
        if kind not in _EVENT_COLORS:
            raise ValueError(f"неизвестный тип события мини-графика: {kind!r}")
        if not label.strip():
            raise ValueError("подпись события мини-графика не должна быть пустой")
        self._events.append((t, label, kind))
        self._render()

    def inspect_at_time(self, t: float) -> str:
        """Возвращает ближайшие значения для позиции времени на графике."""
        if not self._samples:
            return ""
        visible = [
            sample for sample in self._samples if sample[0] >= self._samples[-1][0] - self._window_s
        ]
        if not visible:
            return ""
        sample_t, values = min(visible, key=lambda sample: abs(sample[0] - t))
        age = self._samples[-1][0] - sample_t
        lines = [tr(f"−{age:.1f} с", f"−{age:.1f} s")]
        for key in self._selected:
            value = values[key]
            if value is None:
                continue
            title_en, title_ru, _, _ = _METRICS[key]
            unit = tr("МГц", "MHz") if key == "freq" else ("°C" if key == "temp" else "%")
            label_ru, label_en = title_ru.split(",")[0], title_en.split(",")[0]
            lines.append(f"{tr(label_ru, label_en)}: {value:.0f} {unit}")
        for event_t, label, _kind in self._events:
            if abs(event_t - sample_t) <= 2.0 and event_t >= self._samples[-1][0] - self._window_s:
                lines.append(f"{tr('Событие', 'Event')}: {label}")
        return "\n".join(lines)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (имя Qt API)
        """Показывает значения ближайшего снимка под курсором."""
        chart = self.chart()
        position = chart.mapFromScene(self.mapToScene(event.position().toPoint()))
        if chart.plotArea().contains(position):
            first = next(
                (series for key in self._selected for series in self._series[key]),
                None,
            )
            if first is not None:
                t = chart.mapToValue(position, first).x()
                self._inspector_text = self.inspect_at_time(t)
                if self._inspector_text:
                    QToolTip.showText(event.globalPosition().toPoint(), self._inspector_text, self)
        else:
            self._inspector_text = ""
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def push(self, t: float, snap: TelemetrySnapshot) -> None:
        """Добавляет текущий снимок, оставляя разрыв после пауз и пропусков."""
        values: dict[str, float | None] = {
            "temp": snap.gpu_temp_c,
            "power": snap.gpu_power_percent,
            "load": snap.gpu_load_percent,
            "vram": snap.vram_percent,
            "freq": snap.gpu_freq_mhz,
        }
        if self._last_t is not None and t - self._last_t > _GAP_MAX_S:
            self._samples.append((t, {key: None for key in values}))
        self._samples.append((t, values))
        self._last_t = t
        self._render()

    def _render(self) -> None:
        chart = self.chart()
        now = self._samples[-1][0] if self._samples else float(self._window_s)
        left_values: list[float] = []
        right_values: list[float] = []

        for key in MINI_CHART_SERIES:
            title_en, title_ru, color, right_axis = _METRICS[key]
            visible = key in self._selected
            samples = [(t, values[key]) for t, values in self._samples if t >= now - self._window_s]
            segments: list[list[QPointF]] = []
            segment: list[QPointF] = []
            previous_t: float | None = None
            for t, value in samples:
                if value is None:
                    if segment:
                        segments.append(segment)
                        segment = []
                    previous_t = t
                    continue
                if previous_t is not None and t - previous_t > _GAP_MAX_S:
                    if segment:
                        segments.append(segment)
                    segment = []
                point = QPointF(t, value)
                segment.append(point)
                (right_values if right_axis else left_values).append(value)
                previous_t = t
            if segment:
                segments.append(segment)

            existing = self._series[key]
            while len(existing) < len(segments):
                series = QLineSeries()
                series.setPen(QPen(color, 2))
                chart.addSeries(series)
                series.attachAxis(self._axis_x)
                series.attachAxis(self._axis_right if right_axis else self._axis_left)
                existing.append(series)
            while len(existing) > len(segments):
                series = existing.pop()
                chart.removeSeries(series)
                series.deleteLater()
            for index, (series, points) in enumerate(zip(existing, segments, strict=False)):
                series.setName(tr(title_ru, title_en) if index == 0 else "")
                series.replace(points)
                series.setVisible(visible)
                if index > 0:
                    markers = chart.legend().markers(series)
                    for marker in markers:
                        marker.setVisible(False)

        self._axis_x.setMin(max(0.0, now - self._window_s))
        self._axis_x.setMax(max(float(self._window_s), now))
        self._axis_left.setRange(0.0, max(100.0, _nice_max(left_values)))
        self._axis_right.setRange(0.0, max(3300.0, _nice_max(right_values)))
        visible_events = [
            event for event in self._events if now - self._window_s <= event[0] <= now
        ]
        while len(self._event_series) < len(visible_events):
            series = QLineSeries()
            chart.addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_left)
            self._event_series.append(series)
        while len(self._event_series) > len(visible_events):
            series = self._event_series.pop()
            chart.removeSeries(series)
            series.deleteLater()
        axis_max = self._axis_left.max()
        for series, (event_t, _label, kind) in zip(
            self._event_series, visible_events, strict=False
        ):
            series.setName("")
            series.setPen(QPen(_EVENT_COLORS[kind], 2, Qt.PenStyle.DotLine))
            series.replace([QPointF(event_t, axis_max * 0.82), QPointF(event_t, axis_max * 0.98)])
            for marker in chart.legend().markers(series):
                marker.setVisible(False)


def _nice_max(values: list[float]) -> float:
    """Подбирает читаемый верхний предел оси."""
    maximum = max(values, default=0.0)
    return float(((int(maximum) + 9) // 10) * 10) if maximum > 0 else 0.0


__all__ = [
    "DEFAULT_MINI_CHART_SERIES",
    "DEFAULT_MINI_CHART_SIZE",
    "DEFAULT_MINI_CHART_WINDOW",
    "MINI_CHART_SERIES",
    "MINI_CHART_SIZES",
    "MINI_CHART_WINDOWS",
    "MiniChart",
    "normalize_mini_chart_series",
]
