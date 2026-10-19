"""Графики GUI на PySide6.QtCharts (read-only визуализация телеметрии и V/F)."""

from __future__ import annotations

import math
from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen

from arctuner.tuner import (
    VF_MAX_FREQ_MHZ,
    VF_MAX_VOLTAGE_MV,
    VF_MIN_FREQ_MHZ,
    VF_MIN_VOLTAGE_MV,
)

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
        # Адаптивные потолки: стартовые шкалы (0…100 и 0…3300) расширяются,
        # если данные вышли за них — иначе точки молча обрезались бы по краю.
        self._raise_axis(self._axis_left, max(temp, power), 10.0)
        self._raise_axis(self._axis_right, freq, 100.0)

    def _raise_axis(self, axis: QValueAxis, value: float, step: float) -> None:
        """Поднимает максимум оси, если данные вышли за текущий предел.

        Только вверх, без сужения: ненадолго вернувшийся к норме датчик не должен
        дёргать шкалу каждый тик (не даём осциллировать).
        """
        if value > axis.max():
            axis.setMax(float(math.ceil(value / step) * step))

    @staticmethod
    def _replace(series: QLineSeries, xs, ys) -> None:
        series.replace([QPointF(x, y) for x, y in zip(xs, ys, strict=True)])

    def clear(self) -> None:
        self._samples.clear()
        for series in (self._temp_series, self._power_series, self._freq_series):
            series.clear()


_VF_GRAB_PX = 14.0  # радиус захвата точки на графике, пиксели


class VfChart(QChartView):
    """Кривая V/F: частота (МГц) по X, напряжение (мВ) по Y.

    В editable-режиме точку можно потянуть мышью: и Y (напряжение), и X (частота)
    зажимаются жёсткими границами проекта и соседними точками — кривая остаётся
    монотонной при любом драге. Изменённые точки отдаёт сигнал curve_edited;
    запись остаётся явной — VfTab показывает предпросмотр и подтверждает
    (как у CSV-пути).
    """

    curve_edited = Signal(list)  # list[tuple[int, int]] (мВ, МГц) после правки

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

        self._points: list[tuple[float, float]] = []  # (мВ, МГц), отсортированы по МГц
        self._editable = False
        self._drag_idx: int | None = None

    def set_curve(self, points: list[tuple[int, int]]) -> None:
        """Строит кривую по парам (мВ, МГц); по X идёт частота — так монотонность видна глазом."""
        ordered = sorted(points, key=lambda p: p[1])
        self._points = [(float(mv), float(freq)) for mv, freq in ordered]
        self._series.replace([QPointF(freq, mv) for mv, freq in self._points])
        if ordered:
            freqs = [p[1] for p in ordered]
            mvs = [p[0] for p in ordered]
            pad_x = max(50.0, (max(freqs) - min(freqs)) * 0.05)
            pad_y = max(20.0, (max(mvs) - min(mvs)) * 0.1)
            self._axis_x.setRange(min(freqs) - pad_x, max(freqs) + pad_x)
            self._axis_y.setRange(min(mvs) - pad_y, max(mvs) + pad_y)

    def set_editable(self, editable: bool) -> None:
        """Включает/выключает правку точек мышью (курсор — подсказка)."""
        self._editable = editable
        self._drag_idx = None
        self.setCursor(Qt.CursorShape.CrossCursor if editable else Qt.CursorShape.ArrowCursor)

    def editable(self) -> bool:
        """Правка точек мышью включена?"""
        return self._editable

    # --- программная правка (тот же код, что и у мыши) ---

    def edit_point(self, index: int, mv: float, freq_mhz: float) -> bool:
        """Правит точку index с теми же клампами, что и драг. False — вне диапазона/не editable."""
        if not self._editable or not 0 <= index < len(self._points):
            return False
        clamped_mv, clamped_freq = self._clamp_point(index, mv, freq_mhz)
        self._apply_edit(index, clamped_mv, clamped_freq)
        return True

    def _edited_pairs(self) -> list[tuple[int, int]]:
        """Текущие точки кривой как целые (мВ, МГц)."""
        return [(int(mv), int(freq)) for mv, freq in self._points]

    def _clamp_point(self, index: int, mv: float, freq_mhz: float) -> tuple[float, float]:
        """Границы проекта + монотонность: точка не может пересечь соседей ни по Y, ни по X.

        Напряжение зажимается между напряжениями соседних точек — иначе кривая
        стала бы не-монотонной (нестабильный регион), и правку не приняли бы.
        """
        mv = round(min(max(mv, VF_MIN_VOLTAGE_MV), VF_MAX_VOLTAGE_MV))
        freq = round(min(max(freq_mhz, VF_MIN_FREQ_MHZ), VF_MAX_FREQ_MHZ))
        if index > 0:
            freq = max(freq, round(self._points[index - 1][1]) + 1)
            mv = max(mv, self._points[index - 1][0])
        if index < len(self._points) - 1:
            freq = min(freq, round(self._points[index + 1][1]) - 1)
            mv = min(mv, self._points[index + 1][0])
        freq = round(min(max(freq, VF_MIN_FREQ_MHZ), VF_MAX_FREQ_MHZ))
        return float(mv), float(freq)

    def _apply_edit(self, index: int, mv: float, freq: float) -> None:
        """Обновляет точку в данных и на графике, извещает подписчиков (live)."""
        self._points[index] = (mv, freq)
        self._series.replace(index, QPointF(freq, mv))
        self.curve_edited.emit(self._edited_pairs())

    # --- мышь ---

    def _nearest_index(self, pos: QPointF) -> int | None:
        """Индекс ближайшей точки в радиусе _VF_GRAB_PX (в пикселях), или None."""
        best: int | None = None
        best_d = _VF_GRAB_PX * _VF_GRAB_PX
        for i, (mv, freq) in enumerate(self._points):
            px = self.chart().mapToPosition(QPointF(freq, mv), self._series)
            d = (px.x() - pos.x()) ** 2 + (px.y() - pos.y()) ** 2
            if d <= best_d:
                best_d = d
                best = i
        return best

    def mousePressEvent(self, e) -> None:  # noqa: N802 (название Qt)
        if self._editable and e.button() == Qt.MouseButton.LeftButton:
            idx = self._nearest_index(e.position())
            if idx is not None:
                self._drag_idx = idx
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 (название Qt)
        if self._drag_idx is not None:
            value = self.chart().mapToValue(e.position(), self._series)
            clamped_mv, clamped_freq = self._clamp_point(self._drag_idx, value.y(), value.x())
            self._apply_edit(self._drag_idx, clamped_mv, clamped_freq)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 (название Qt)
        if self._drag_idx is not None and e.button() == Qt.MouseButton.LeftButton:
            self._drag_idx = None
            self.curve_edited.emit(self._edited_pairs())
            e.accept()
            return
        super().mouseReleaseEvent(e)
