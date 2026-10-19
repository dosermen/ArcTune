"""Вкладка «Мониторинг»: датчики + живой график (read-only)."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from arctuner.gui.charts import LiveChart
from arctuner.gui.widgets import MetricCard
from arctuner.igcl.api import TelemetrySnapshot

#: пауза дольше этого считается пропуском данных (сон/подвисание) — график
#: обнуляется, чтобы линия не «мостила» через паузу фальшивым значением.
GAP_MAX_S = 5.0


class MonitoringTab(QWidget):
    """Показывает текущую телеметрию и скроллящийся график последних минут."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t0 = time.monotonic()
        self._t_last: float | None = None

        self._freq = MetricCard("Ядро", "МГц", accent="freq")
        self._voltage = MetricCard("Напряжение", "В")
        self._temp = MetricCard("Температура", "°C", accent="temp")
        self._power = MetricCard("Питание", "%", accent="power")

        self._vram_freq = MetricCard("Память", "Гбит/с", accent="freq")
        self._vram_voltage = MetricCard("Память, V", "В")
        self._vram_temp = MetricCard("Память, T", "°C", accent="temp")
        self._vram_power = MetricCard("Память, Вт", "Вт", accent="power")

        self._activity = MetricCard("Активность", "счётчик")
        self._gpu_vr = MetricCard("VR ядра", "°C", accent="temp")
        self._vram_vr = MetricCard("VR памяти", "°C", accent="temp")
        self._sa_vr = MetricCard("VR SA", "°C", accent="temp")

        grid = QGridLayout()
        grid.setSpacing(8)
        for col, card in enumerate((self._freq, self._voltage, self._temp, self._power)):
            grid.addWidget(card, 0, col)
        for col, card in enumerate(
            (self._vram_freq, self._vram_voltage, self._vram_temp, self._vram_power)
        ):
            grid.addWidget(card, 1, col)
        for col, card in enumerate((self._activity, self._gpu_vr, self._vram_vr, self._sa_vr)):
            grid.addWidget(card, 2, col)

        self._limits = QLabel("Ограничение: —")
        self._limits.setObjectName("limitsLabel")
        self._limits.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._chart = LiveChart(window_s=300)
        self._chart.setMinimumHeight(220)

        chart_row = QHBoxLayout()
        chart_row.addWidget(QLabel("График (°C / % слева, МГц справа, окно 5 мин):"))
        chart_row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(self._limits)
        lay.addLayout(chart_row)
        lay.addWidget(self._chart, stretch=1)

    def on_telemetry(self, snap: TelemetrySnapshot) -> None:
        """Обновляет карточки и график по новому снимку."""
        self._freq.set_value(snap.gpu_freq_mhz)
        self._voltage.set_value(snap.gpu_voltage_v, digits=3)
        self._temp.set_value(snap.gpu_temp_c)
        # Ватты — средняя мощность из счётчиков энергии (PowerMeter); пока она
        # не накопилась (первый тик) — честный процент от лимита.
        if snap.gpu_power_w is not None:
            pct = f"{snap.gpu_power_percent:g} %" if snap.gpu_power_percent is not None else ""
            self._power.set_value(snap.gpu_power_w, digits=0, unit="Вт", tooltip=pct)
        else:
            self._power.set_value(snap.gpu_power_percent)

        self._vram_freq.set_value(snap.vram_eff_freq_mbps, digits=2)
        self._vram_voltage.set_value(snap.vram_voltage_v, digits=3)
        self._vram_temp.set_value(snap.vram_temp_c)
        # Ватты памяти — тот же PowerMeter, третий счётчик; до второго тика — «—».
        self._vram_power.set_value(snap.vram_power_w)
        # VR-температуры: перегрев фаз питания не виден по датчикам ядра/памяти.
        self._gpu_vr.set_value(snap.gpu_vr_temp_c)
        self._vram_vr.set_value(snap.vram_vr_temp_c)
        self._sa_vr.set_value(snap.sa_vr_temp_c)
        # Счётчик активности — int64, растёт всё время работы драйвера: сырой
        # 13-значный номер нечитаем, разделяем разряды пробелами.
        counter = snap.activity_counter
        self._activity.set_value(None if counter is None else f"{counter:,}".replace(",", " "))

        self._limits.setText(f"Ограничение: {snap.limit_summary()}")

        # На график кладём только полные снимки — частичные точки соврали бы картинку.
        if None not in (snap.gpu_temp_c, snap.gpu_power_percent, snap.gpu_freq_mhz):
            t = time.monotonic() - self._t0
            if self._t_last is not None and t - self._t_last > GAP_MAX_S:
                # Долгий перерыв (сон/занятый воркер): линия через паузу — ложь.
                self._chart.clear()
            self._chart.push(t, snap.gpu_temp_c, snap.gpu_power_percent, snap.gpu_freq_mhz)
            self._t_last = t


__all__ = ["MonitoringTab"]
