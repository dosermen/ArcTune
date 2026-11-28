"""Графики GUI на PySide6.QtCharts (read-only визуализация телеметрии и V/F)."""

from __future__ import annotations

import math
from collections import deque
from typing import Protocol

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QLineSeries,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen

from arctuner.gui.chartstyle import (
    DEFAULT_CHART_STYLE,
    GlowLayer,
    PulseController,
    apply_chart_style,
    chart_style,
    force_theme,
)
from arctuner.gui.theme import (
    SERIES_FPS,
    SERIES_FREQ,
    SERIES_LOAD,
    SERIES_MEDIA,
    SERIES_POWER,
    SERIES_TEMP,
    SERIES_VRAM,
    load_theme,
)
from arctuner.i18n import tr
from arctuner.tuner import (
    VF_MAX_FREQ_MHZ,
    VF_MAX_VOLTAGE_MV,
    VF_MIN_FREQ_MHZ,
    VF_MIN_VOLTAGE_MV,
)


class ThemedChart(Protocol):
    """Минимальный контракт «график, умеющий менять тему и стиль вида».

    LiveChart, VfChart и MiniChart наследуют QChartView и реализуют set_dark
    и set_chart_style. Protocol нужен, чтобы findChildren(QChartView) (базовый
    тип) позволял вызывать эти методы без подавления ошибок mypy.
    """

    def set_dark(self, dark: bool) -> None: ...

    def set_chart_style(self, name: str) -> None:
        """Накладывает пресет вида (см. gui.chartstyle) на график."""
        ...


# Цвета серий (темп/мощность/частота) — одинаковы и в легенде, и на графике.
# Значения приходят из theme.py (единый источник с маркерами карточек).
_TEMP_COLOR = QColor(SERIES_TEMP)
_POWER_COLOR = QColor(SERIES_POWER)
_FREQ_COLOR = QColor(SERIES_FREQ)
_LOAD_COLOR = QColor(SERIES_LOAD)
_VRAM_COLOR = QColor(SERIES_VRAM)
_MEDIA_COLOR = QColor(SERIES_MEDIA)

#: минимум высоты графика с осями-заголовками: ниже ~250 px QtCharts обрезает
#: заголовок оси в «…» (место оси сжимается под подписи) — 280 с запасом.
_AXIS_MIN_H = 280


class LiveChart(QChartView):
    """Прокручиваемый график телеметрии в реальном времени.

    Левая ось — температура (°C), мощность (%), загрузка ядра (%) и занятость
    памяти (%). Частота ядра (МГц) и FPS используют отдельные правые шкалы.

    Опциональные серии (по умолчанию выключены, создаются лениво): «Медиа, %»
    (группа IGCL MEDIA, свои колонки в _samples) и CPU-серии (отдельный буфер
    _cpu_samples).
    """

    def __init__(self, window_s: int = 300, parent=None) -> None:
        super().__init__(parent)
        self._window_s = window_s
        # Стиль вида (см. chartstyle): ширина линий — из пресета, цвета —
        # канонические SERIES_*; применяется окном (MainWindow) сразу при старте.
        self._style = chart_style(DEFAULT_CHART_STYLE)
        # (t, temp, power, freq, load, vram, media, fps): load/vram/media —
        # None, пока LoadMeter не накопил базу; FPS может отсутствовать до
        # появления кадров в foreground-приложении.
        self._samples: deque[
            tuple[
                float,
                float,
                float,
                float,
                float | None,
                float | None,
                float | None,
                float | None,
            ]
        ] = deque(maxlen=window_s * 2)
        self._cpu_samples: deque[tuple[float, float | None, float | None, float | None]] = deque(
            maxlen=window_s * 2
        )

        self._temp_series = self._make_series(tr("Температура, °C", "Temperature, °C"), _TEMP_COLOR)
        self._power_series = self._make_series(tr("Мощность, %", "Power, %"), _POWER_COLOR)
        self._freq_series = self._make_series(tr("Частота, МГц", "Frequency, MHz"), _FREQ_COLOR)
        self._load_series = self._make_series(tr("Загрузка, %", "GPU Load, %"), _LOAD_COLOR)
        self._vram_series = self._make_series(tr("Память, %", "VRAM Used, %"), _VRAM_COLOR)
        self._cpu_load_series: QLineSeries | None = None
        self._cpu_freq_series: QLineSeries | None = None
        self._cpu_temp_series: QLineSeries | None = None
        self._media_series: QLineSeries | None = None
        self._fps_series: QLineSeries | None = None

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0.0, float(window_s))
        self._axis_x.setTitleText(tr("секунды", "seconds"))

        self._axis_left = QValueAxis()
        self._axis_left.setRange(0.0, 100.0)
        self._axis_left.setTitleText("°C / %")

        self._axis_right = QValueAxis()
        self._axis_right.setRange(0.0, 3300.0)
        self._axis_right.setTitleText(tr("МГц", "MHz"))
        self._axis_fps = QValueAxis()
        self._axis_fps.setRange(0.0, 240.0)
        self._axis_fps.setTitleText("FPS")

        # Подписи всегда полные: QAbstractAxis по умолчанию (truncateLabels=true)
        # заменяет текст на «…», как только места не хватает — на сжатом графике
        # это «…» вместо чисел на ВСЕХ осях сразу (живой баг).
        for axis in (self._axis_x, self._axis_left, self._axis_right, self._axis_fps):
            axis.setTruncateLabels(False)

        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)  # иначе лаги при 1 Гц
        for series in (
            self._temp_series,
            self._power_series,
            self._load_series,
            self._vram_series,
            self._freq_series,
        ):
            chart.addSeries(series)
        chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._axis_left, Qt.AlignmentFlag.AlignLeft)
        chart.addAxis(self._axis_right, Qt.AlignmentFlag.AlignRight)
        chart.addAxis(self._axis_fps, Qt.AlignmentFlag.AlignRight)
        self._temp_series.attachAxis(self._axis_x)
        self._temp_series.attachAxis(self._axis_left)
        self._power_series.attachAxis(self._axis_x)
        self._power_series.attachAxis(self._axis_left)
        self._load_series.attachAxis(self._axis_x)
        self._load_series.attachAxis(self._axis_left)
        self._vram_series.attachAxis(self._axis_x)
        self._vram_series.attachAxis(self._axis_left)
        self._freq_series.attachAxis(self._axis_x)
        self._freq_series.attachAxis(self._axis_right)
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Свечение и пульсация: серии уже в графике — регистрируем в glow-слое
        # (подложки создаются только при включённом стиле, см. set_chart_style).
        self._glow = GlowLayer(chart)
        self._pulse = PulseController(self)
        for series in (
            self._temp_series,
            self._power_series,
            self._load_series,
            self._vram_series,
            self._freq_series,
        ):
            self._glow.track(series)
        # Минимум, при котором оси не сжимаются: заголовок оси («секунды»)
        # обрезается в «…» ниже ~250 px высоты (измерено на Qt 6.12) — 280 с запасом.
        self.setMinimumHeight(_AXIS_MIN_H)

    def _make_series(self, name: str, color: QColor) -> QLineSeries:
        series = QLineSeries()
        series.setName(name)
        series.setPen(QPen(color, self._style.line_width))
        return series

    def push(
        self,
        t: float,
        temp: float,
        power: float,
        freq: float,
        load: float | None = None,
        vram: float | None = None,
        media: float | None = None,
        fps: float | None = None,
    ) -> None:
        """Добавляет снимок и перерисовывает.

        temp/power/freq — только полные значения (без None, иначе точки соврали
        бы картинку). load/vram/media/fps могут отсутствовать: данные ещё не
        накоплены либо источник недоступен; такие точки не рисуются как нули.
        """
        self._samples.append((t, temp, power, freq, load, vram, media, fps))
        self._replace(self._temp_series, 1)
        self._replace(self._power_series, 2)
        self._replace(self._load_series, 4)
        self._replace(self._vram_series, 5)
        self._replace(self._freq_series, 3)
        if self._media_series is not None:  # опциональная серия — лениво
            self._replace(self._media_series, 6)
        if self._fps_series is not None:
            self._replace(self._fps_series, 7)
        # Прокрутка окна: показываем последние window_s секунд.
        self._axis_x.setMax(max(t, float(self._window_s)))
        self._axis_x.setMin(max(0.0, t - self._window_s))
        # Адаптивные потолки: стартовые шкалы (0…100 и 0…3300) расширяются,
        # если данные вышли за них — иначе точки молча обрезались бы по краю.
        left = [temp, power, *(v for v in (load, vram, media) if v is not None)]
        self._raise_axis(self._axis_left, max(left), 10.0)
        self._raise_axis(self._axis_right, freq, 100.0)
        if fps is not None:
            self._raise_axis(self._axis_fps, fps, 10.0)

    def _raise_axis(self, axis: QValueAxis, value: float, step: float) -> None:
        """Поднимает максимум оси, если данные вышли за текущий предел.

        Только вверх, без сужения: ненадолго вернувшийся к норме датчик не должен
        дёргать шкалу каждый тик (не даём осциллировать).
        """
        if value > axis.max():
            axis.setMax(float(math.ceil(value / step) * step))

    def set_cpu_series_visible(self, key: str, visible: bool) -> None:
        """Показывает/скрывает CPU-серию."""
        if key == "cpu_load":
            series = self._cpu_load_series
            if visible and series is None:
                series = self._make_series(tr("CPU, %", "CPU Load, %"), QColor("#26a69a"))
                self.chart().addSeries(series)
                series.attachAxis(self._axis_x)
                series.attachAxis(self._axis_left)
                self._replace_cpu(series, 1)
                self._cpu_load_series = series
                self._glow.track(series)
        elif key == "cpu_freq":
            series = self._cpu_freq_series
            if visible and series is None:
                series = self._make_series(tr("CPU, МГц", "CPU Frequency, MHz"), QColor("#ab47bc"))
                self.chart().addSeries(series)
                series.attachAxis(self._axis_x)
                series.attachAxis(self._axis_right)
                self._replace_cpu(series, 2)
                self._cpu_freq_series = series
                self._glow.track(series)
        elif key == "cpu_temp":
            series = self._cpu_temp_series
            if visible and series is None:
                series = self._make_series(tr("CPU, °C", "CPU Temperature, °C"), QColor("#ef6c00"))
                self.chart().addSeries(series)
                series.attachAxis(self._axis_x)
                series.attachAxis(self._axis_left)
                self._replace_cpu(series, 3)
                self._cpu_temp_series = series
                self._glow.track(series)
        else:
            raise ValueError(f"неизвестная серия CPU: {key!r}")
        if series is not None:
            series.setVisible(visible)

    def set_media_series_visible(self, visible: bool) -> None:
        """Показывает/скрывает опциональную серию «Медиа, %» (группа IGCL MEDIA).

        Серия создаётся лениво при первом включении и догружает уже
        накопленную историю из _samples — выключенной по умолчанию линии нет
        ни на графике, ни в легенде.
        """
        series = self._media_series
        if visible and series is None:
            series = self._make_series(tr("Медиа, %", "Media, %"), _MEDIA_COLOR)
            self.chart().addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_left)
            self._replace(series, 6)
            self._media_series = series
            self._glow.track(series)
        if series is not None:
            series.setVisible(visible)

    def set_fps_series_visible(self, visible: bool) -> None:
        """Показывает/скрывает FPS на отдельной шкале."""
        series = self._fps_series
        if visible and series is None:
            series = self._make_series("FPS", QColor(SERIES_FPS))
            self.chart().addSeries(series)
            series.attachAxis(self._axis_x)
            series.attachAxis(self._axis_fps)
            self._replace(series, 7)
            self._fps_series = series
            self._glow.track(series)
        if series is not None:
            series.setVisible(visible)

    def push_cpu(
        self,
        t: float,
        usage_percent: float | None,
        frequency_mhz: float | None,
        temperature_c: float | None = None,
    ) -> None:
        """Добавляет CPU-метрики к отдельным сериям без изменения GPU-снимков."""
        self._cpu_samples.append((t, usage_percent, frequency_mhz, temperature_c))
        if self._cpu_load_series is not None:
            self._replace_cpu(self._cpu_load_series, 1)
        if self._cpu_freq_series is not None:
            self._replace_cpu(self._cpu_freq_series, 2)
        if self._cpu_temp_series is not None:
            self._replace_cpu(self._cpu_temp_series, 3)
        self._axis_x.setMax(max(t, float(self._window_s)))
        self._axis_x.setMin(max(0.0, t - self._window_s))
        if usage_percent is not None:
            self._raise_axis(self._axis_left, usage_percent, 10.0)
        if frequency_mhz is not None:
            self._raise_axis(self._axis_right, frequency_mhz, 100.0)
        if temperature_c is not None:
            self._raise_axis(self._axis_left, temperature_c, 10.0)

    def _replace_cpu(self, series: QLineSeries, column: int) -> None:
        points = []
        for sample in self._cpu_samples:
            value = sample[column]
            if value is not None:
                points.append(QPointF(sample[0], value))
        series.replace(points)
        self._glow.sync(series)  # у свечения нет сигнала pointsChanged — копируем сами

    def _replace(self, series: QLineSeries, column: int) -> None:
        """Перерисовывает серию по колонке _samples, пропуская None-точки."""
        points: list[QPointF] = []
        for sample in self._samples:
            value = sample[column]
            if value is not None:
                points.append(QPointF(sample[0], value))
        series.replace(points)
        self._glow.sync(series)  # у свечения нет сигнала pointsChanged — копируем сами

    def clear(self) -> None:
        self._samples.clear()
        for series in (
            self._temp_series,
            self._power_series,
            self._load_series,
            self._vram_series,
            self._freq_series,
        ):
            series.clear()
        if self._media_series is not None:
            self._media_series.clear()
        if self._fps_series is not None:
            self._fps_series.clear()
        self.clear_cpu()
        self._glow.sync_all()  # подложки свечения отстают от очищенных серий

    def clear_cpu(self) -> None:
        """Очищает историю только CPU-серий."""
        self._cpu_samples.clear()
        if self._cpu_load_series is not None:
            self._cpu_load_series.clear()
        if self._cpu_freq_series is not None:
            self._cpu_freq_series.clear()
        if self._cpu_temp_series is not None:
            self._cpu_temp_series.clear()
        self._glow.sync_all()  # подложки не должны отставать от очистки (вызывается и вне clear)

    def set_dark(self, dark: bool) -> None:
        """Интентионно no-op: вид графика мониторинга пользователь просил сохранить.

        Белый фон, сетка, легенда и цвета линий не меняются при переключении
        темы — это «приборная панель», её внематериальный вид стабильнее.
        Задать другой вид может только стиль графиков (set_chart_style).
        """
        del dark

    def set_chart_style(self, name: str) -> None:
        """Накладывает пресет вида (см. gui.chartstyle) на график мониторинга.

        «Классика» задаёт светлые кисти явно (график не зависит от темы
        приложения — см. set_dark); остальные пресеты — свои фоны/сетку.
        Pen'ы серий не перекрашиваются: тема QChart здесь никогда не меняется,
        поэтому остаются канонические цвета SERIES_* — правится только ширина.
        """
        spec = chart_style(name)
        self._style = spec
        apply_chart_style(self.chart(), spec)
        for series in self._glow.tracked:
            pen = series.pen()
            pen.setWidthF(spec.line_width)
            series.setPen(pen)
        self._glow.set_active(spec.glow)
        self._pulse.set_style(spec)


_VF_GRAB_PX = 14.0  # радиус захвата точки на графике, пиксели
_VF_MARKER_SIZE = 15.0  # базовый размер маркеров точек V/F (px)


class VfChart(QChartView):
    """Кривая V/F: частота (МГц) по X, напряжение (мВ) по Y.

    В editable-режиме точку можно потянуть мышью: и Y (напряжение), и X (частота)
    зажимаются жёсткими границами проекта и соседними точками — кривая остаётся
    монотонной при любом драге. Изменённые точки отдаёт сигнал curve_edited;
    запись остаётся явной — VfTab показывает предпросмотр и подтверждает
    (как у CSV-пути).

    Пока кривая отличается от эталона, под основной линией лежит тень
    (призрак) — пунктирный серый дублёр кривой до правки (set_ghost):
    видно, что именно изменилось. Тень не участвует в свечении (glow)
    и не попадает в легенду.
    """

    curve_edited = Signal(list)  # list[tuple[int, int]] (мВ, МГц) после правки

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._style = chart_style(DEFAULT_CHART_STYLE)
        self._series = QLineSeries()
        self._series.setName("V/F")
        self._series.setPen(QPen(_FREQ_COLOR, self._style.line_width))
        self._axis_x = QValueAxis()
        self._axis_x.setTitleText(tr("МГц", "MHz"))
        self._axis_y = QValueAxis()
        self._axis_y.setTitleText(tr("мВ", "mV"))
        # Подписи всегда полные — та же причина, что у LiveChart (см. выше).
        for axis in (self._axis_x, self._axis_y):
            axis.setTruncateLabels(False)

        # Отдельный scatter-серия для отображения маркеров точек (не сливается с линией)
        self._scatter = QScatterSeries()
        self._scatter.setName(tr("Точки", "Points"))

        # Тень (призрак) кривой до правки: добавляется ПЕРЕД основной серией —
        # QtCharts рисует в порядке добавления, тень обязана лежать под линией.
        self._ghost = QLineSeries()
        self._ghost.setName("")
        self._ghost.setPen(self._ghost_pen())
        self._ghost.setVisible(False)

        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.addSeries(self._ghost)
        chart.addSeries(self._series)
        chart.addSeries(self._scatter)
        chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        self._ghost.attachAxis(self._axis_x)
        self._ghost.attachAxis(self._axis_y)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)
        self._scatter.attachAxis(self._axis_x)
        self._scatter.attachAxis(self._axis_y)
        chart.legend().setVisible(False)
        self.setChart(chart)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Свечение/пульсация кривой: scatter-маркеры подложек не получают
        # (см. set_chart_style — для них меняется только размер маркера).
        self._glow = GlowLayer(chart)
        self._glow.track(self._series)
        self._pulse = PulseController(self)
        # Минимум под заголовки осей — та же причина, что у LiveChart.
        self.setMinimumHeight(_AXIS_MIN_H)

        self._points: list[tuple[float, float]] = []  # (мВ, МГц), отсортированы по МГц
        self._editable = False
        self._drag_idx: int | None = None

    def set_curve(self, points: list[tuple[int, int]]) -> None:
        """Строит кривую по парам (мВ, МГц); по X идёт частота — так монотонность видна глазом."""
        ordered = sorted(points, key=lambda p: p[1])
        self._points = [(float(mv), float(freq)) for mv, freq in ordered]
        # Линия кривой + маркеры точек
        self._series.replace([QPointF(freq, mv) for mv, freq in self._points])
        self._scatter.replace([QPointF(freq, mv) for mv, freq in self._points])
        self._glow.sync(self._series)  # подложка свечения копирует точки сама
        self._fit_axes()

    def set_ghost(self, points: list[tuple[int, int]] | None) -> None:
        """Тень (призрак) кривой до правки: серый пунктир под основной линией.

        None/пусто — тень гаснет. Точка тени может лежать за пределами
        осей (после записи оси пересчитываются по новой кривой) — поэтому
        диапазон осей включает и видимую тень (см. _fit_axes).
        """
        if not points:
            if self._ghost.isVisible():
                self._ghost.replace([])
                self._ghost.setVisible(False)
                self._fit_axes()
            return
        ordered = sorted(points, key=lambda p: p[1])
        desired = [QPointF(freq, mv) for mv, freq in ordered]
        if self._ghost.isVisible() and list(self._ghost.points()) == desired:
            return  # тень уже показана и не изменилась — оси не дёргаем
        self._ghost.replace(desired)
        self._ghost.setVisible(True)
        self._fit_axes()

    def _ghost_pen(self) -> QPen:
        """Перо тени: серый полупрозрачный пунктир — «кривая до правки»."""
        pen = QPen(QColor(150, 150, 150, 170), max(1.0, self._style.line_width))
        pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _fit_axes(self) -> None:
        """Диапазон осей: основная кривая + видимая тень (тень не должна резаться)."""
        pts = list(self._points)
        if self._ghost.isVisible():
            pts.extend((p.y(), p.x()) for p in self._ghost.points())  # QPointF(freq, mv)
        if not pts:
            return
        freqs = [p[1] for p in pts]
        mvs = [p[0] for p in pts]
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

    def set_dark(self, dark: bool) -> None:
        """Подгоняет фон/сетку кривой V/F под тему приложения.

        Встроенная тема QChart перекрашивает серии — после неё вернём свой
        синий (#4a90d9): тот же цвет, что у маркера карточек «Ядро/Память»,
        и пунктирное перо тени (у тени тема тоже сбрасывает dash).
        """
        self.chart().setTheme(
            QChart.ChartTheme.ChartThemeDark if dark else QChart.ChartTheme.ChartThemeLight
        )
        self._series.setPen(QPen(_FREQ_COLOR, self._style.line_width))
        self._ghost.setPen(self._ghost_pen())

    def set_chart_style(self, name: str) -> None:
        """Накладывает пресет вида (см. gui.chartstyle) на кривую V/F.

        «Классика» = тема приложения (форс-тогл сбрасывает overrides прошлого
        пресета); остальные пресеты живут поверх фиксированной тёмной базы,
        чтобы неон выглядел одинаково при любой теме окна. setTheme
        перекрашивает pen'ы — линию возвращаем к #4a90d9, маркеры — к базовому
        размеру (для glow — крупнее, точки читаются на тёмном фоне).
        """
        spec = chart_style(name)
        self._style = spec
        if spec.name == "classic":
            force_theme(self.chart(), load_theme() == "dark")
        else:
            force_theme(self.chart(), True)  # тёмная база под неоновыми кистями
            apply_chart_style(self.chart(), spec)
        self._series.setPen(QPen(_FREQ_COLOR, spec.line_width))
        self._ghost.setPen(self._ghost_pen())  # force_theme перекрасил и тень
        scatter_pen = self._scatter.pen()  # цвет маркеров даёт тема — только ширина
        scatter_pen.setWidthF(spec.line_width)
        self._scatter.setPen(scatter_pen)
        self._scatter.setMarkerSize(_VF_MARKER_SIZE * (1.6 if spec.glow else 1.0))
        self._glow.set_active(spec.glow)
        self._pulse.set_style(spec)

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
        self._scatter.replace(index, QPointF(freq, mv))
        self._glow.sync(self._series)  # подложка свечения копирует точки сама
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
