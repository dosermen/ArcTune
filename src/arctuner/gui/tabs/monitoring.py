"""Вкладка «Мониторинг»: датчики + живой график (read-only)."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from arctuner.gui.charts import LiveChart
from arctuner.gui.widgets import MetricCard
from arctuner.igcl.api import TelemetrySnapshot


class MonitoringTab(QWidget):
    """Показывает текущую телеметрию и скроллящийся график последних минут."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t0 = time.monotonic()

        self._freq = MetricCard("Ядро", "МГц")
        self._voltage = MetricCard("Напряжение", "В")
        self._temp = MetricCard("Температура", "°C")
        self._power = MetricCard("Питание", "%")

        self._vram_freq = MetricCard("Память", "Гбит/с")
        self._vram_voltage = MetricCard("Память, V", "В")
        self._vram_temp = MetricCard("Память, T", "°C")
        self._activity = MetricCard("Активность", "счётчик")

        grid = QGridLayout()
        grid.setSpacing(8)
        for col, card in enumerate((self._freq, self._voltage, self._temp, self._power)):
            grid.addWidget(card, 0, col)
        for col, card in enumerate(
            (self._vram_freq, self._vram_voltage, self._vram_temp, self._activity)
        ):
            grid.addWidget(card, 1, col)

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
        self._power.set_value(snap.gpu_power_percent)

        self._vram_freq.set_value(snap.vram_eff_freq_mbps, digits=2)
        self._vram_voltage.set_value(snap.vram_voltage_v, digits=3)
        self._vram_temp.set_value(snap.vram_temp_c)
        self._activity.set_value(snap.activity_counter)

        self._limits.setText(f"Ограничение: {snap.limit_summary()}")

        # На график кладём только полные снимки — частичные точки соврали бы картинку.
        if None not in (snap.gpu_temp_c, snap.gpu_power_percent, snap.gpu_freq_mhz):
            t = time.monotonic() - self._t0
            self._chart.push(t, snap.gpu_temp_c, snap.gpu_power_percent, snap.gpu_freq_mhz)


__all__ = ["MonitoringTab"]
