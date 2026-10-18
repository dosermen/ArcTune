"""Графики GUI на PySide6.QtCharts (read-only визуализация телеметрии и V/F)."""

from __future__ import annotations

from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

# Цвета серий (темп/мощность/частота) — одинаковы и в легенде, и на графике.
_TEMP_COLOR = QColor("#e05555")
_POWER_COLOR = QColor("#e0a13a")
_FREQ_COLOR = QColor("#4a90d9")


class LiveChart(QChartView):
    """Прокручиваемый график телеметрии в реальном времени.

    Левая ось — температура (°C) и мощность (%), обе шкалы 0…100, поэтому
    делят одну ось. Правая ось — частота ядра (МГц), отдельная шкала, чтобы
    частота не «прилипала» к низу из-за разницы масштабов.
    """

    def __init__(self, window_s: int = 300, parent=None) -> None:
        super().__init__(parent)
        self._window_s = window_s
        # (t, temp, power, freq) — только полные снимки, без частичных точек.
        self._samples: deque[tuple[float, float, float, float]] = deque(maxlen=window_s * 2)

        self._temp_series = self._make_series("Температура, °C", _TEMP_COLOR)
        self._power_series = self._make_series("Мощность, %", _POWER_COLOR)
        self._freq_series = self._make_series("Частота, МГц", _FREQ_COLOR)

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0.0, float(window_s))
        self._axis_x.setTitleText("секунды")

        self._axis_left = QValueAxis()
        self._axis_left.setRange(0.0, 100.0)
        self._axis_left.setTitleText("°C / %")

        self._axis_right = QValueAxis()
        self._axis_right.setRange(0.0, 3300.0)
        self._axis_right.setTitleText("МГц")

        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)  # иначе лаги при 1 Гц
        for series in (self._temp_series, self._power_series, self._freq_series):
            chart.addSeries(series)
        chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._axis_left, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(self._axis_right, Qt.AlignmentFlag.AlignRight)
        self._temp_series.attachAxis(self._axis_x)
        self._temp_series.attachAxis(self._axis_left)
        self._power_series.attachAxis(self._axis_x)
        self._power_series.attachAxis(self._axis_left)
        self._freq_series.attachAxis(self._axis_x)
        self._freq_series.attachAxis(self._axis_right)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

    @staticmethod
    def _make_series(name: str, color: QColor) -> QLineSeries:
        series = QLineSeries()
        series.setName(name)
        series.setPen(QPen(color, 2))
        return series

    def push(self, t: float, temp: float, power: float, freq: float) -> None:
        """Добавляет снимок и перерисовывает. Частичные значения (None) не передаются."""
        self._samples.append((t, temp, power, freq))
        self._replace(
            self._temp_series, (s[0] for s in self._samples), (s[1] for s in self._samples)
        )
        self._replace(
            self._power_series, (s[0] for s in self._samples), (s[2] for s in self._samples)
        )
        self._replace(
            self._freq_series, (s[0] for s in self._samples), (s[3] for s in self._samples)
        )
        # Прокрутка окна: показываем последние window_s секунд.
        self._axis_x.setMax(max(t, float(self._window_s)))
        self._axis_x.setMin(max(0.0, t - self._window_s))

    @staticmethod
    def _replace(series: QLineSeries, xs, ys) -> None:
        series.replace([QPointF(x, y) for x, y in zip(xs, ys, strict=True)])

    def clear(self) -> None:
        self._samples.clear()
        for series in (self._temp_series, self._power_series, self._freq_series):
            series.clear()


class VfChart(QChartView):
    """Кривая V/F: частота (МГц) по X, напряжение (мВ) по Y."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series = QLineSeries()
        self._series.setName("V/F")
        self._series.setPen(QPen(_FREQ_COLOR, 2))
        self._axis_x = QValueAxis()
        self._axis_x.setTitleText("МГц")
        self._axis_y = QValueAxis()
        self._axis_y.setTitleText("мВ")

        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.addSeries(self._series)
        chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)
        chart.legend().setVisible(False)
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)

    def set_curve(self, points: list[tuple[int, int]]) -> None:
        """Строит кривую по парам (мВ, МГц); по X идёт частота — так монотонность видна глазом."""
        ordered = sorted(points, key=lambda p: p[1])
        self._series.replace([QPointF(freq, mv) for mv, freq in ordered])
        if ordered:
            freqs = [p[1] for p in ordered]
            mvs = [p[0] for p in ordered]
            pad_x = max(50.0, (max(freqs) - min(freqs)) * 0.05)
            pad_y = max(20.0, (max(mvs) - min(mvs)) * 0.1)
            self._axis_x.setRange(min(freqs) - pad_x, max(freqs) + pad_x)
            self._axis_y.setRange(min(mvs) - pad_y, max(mvs) + pad_y)
