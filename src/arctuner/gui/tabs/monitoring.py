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


#: пояснения датчиков (tooltip при наведении, окно по клику на карточку):
#: честно «что это» и «зачем следить», без угадывания.
_NOTES: dict[str, str] = {
    "freq": "Фактическая частота графического ядра. При троттлинге (Power/Temperature "
    "Limited) падает ниже заданной — сравнивайте с плашкой «Ограничение».",
    "voltage": "Напряжение ядра. Смещение в плюс — вкладка «Разгон»; тонкая настройка — "
    "кривая V/F.",
    "temp": "Температура кристалла GPU — главный датчик нагрева. По ней работает лимит "
    "температуры и троттлинг. Ориентир проекта: держать <80 °C под нагрузкой.",
    "power": "Мощность: % от текущего лимита питания; ватты — средняя из счётчиков "
    "энергии (появляются со 2-го тика опроса).",
    "vram_freq": "Фактическая эффективная скорость памяти GDDR6 (номинал B580 — 20 Гбит/с).",
    "vram_voltage": "Напряжение памяти. На B580 фиксировано на 1.35 В — драйвер не даёт его менять.",
    "vram_temp": "Температура чипов GDDR6 (6× Samsung SC20). Перегрев памяти → ошибки → "
    "артефакты и вылеты. Ключевой датчик при разгоне памяти.",
    "vram_power": "Мощность памяти в ваттах — из счётчика энергии vramEnergyCounter "
    "(read-only, честные джоули).",
    "activity": "Накопленное время занятости GPU (секунды активной работы). Растёт "
    "только под нагрузкой; в простое счётчик может стоять на месте — это норма, "
    "а не сбой. Нужен для дельт и реплея инцидентов.",
    "gpu_vr": "Температура фаз питания (VRM) ядра — мосфеты на плате. Греются от тока, а "
    "не от вычислений: перегрев фаз по кристаллу не виден.",
    "vram_vr": "Температура фаз питания памяти. В простое часто самая горячая из пяти "
    "(на живой B580 ~64 °C при 45–48 °C кристалла).",
    "sa_vr": "Температура фаз питания SA (System Agent — домен контроллера/обвязки "
    "кристалла). Обычно наименее нагруженная.",
}


class MonitoringTab(QWidget):
    """Показывает текущую телеметрию и скроллящийся график последних минут."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t0 = time.monotonic()
        self._t_last: float | None = None

        self._freq = MetricCard("Ядро", "МГц", accent="freq", note=_NOTES["freq"])
        self._voltage = MetricCard("Напряжение", "В", note=_NOTES["voltage"])
        self._temp = MetricCard("Температура", "°C", accent="temp", note=_NOTES["temp"])
        self._power = MetricCard("Питание", "%", accent="power", note=_NOTES["power"])

        self._vram_freq = MetricCard("Память", "Гбит/с", accent="freq", note=_NOTES["vram_freq"])
        self._vram_voltage = MetricCard("Память, V", "В", note=_NOTES["vram_voltage"])
        self._vram_temp = MetricCard("Память, T", "°C", accent="temp", note=_NOTES["vram_temp"])
        self._vram_power = MetricCard("Память, Вт", "Вт", accent="power", note=_NOTES["vram_power"])

        self._activity = MetricCard("Активность", "с", note=_NOTES["activity"])
        self._gpu_vr = MetricCard("VR ядра", "°C", accent="temp", note=_NOTES["gpu_vr"])
        self._vram_vr = MetricCard("VR памяти", "°C", accent="temp", note=_NOTES["vram_vr"])
        self._sa_vr = MetricCard("VR SA", "°C", accent="temp", note=_NOTES["sa_vr"])

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
        self._limits.setToolTip(
            "Активный лимитер прямо сейчас: Power / Temperature / Current / Voltage / "
            "Utilization Limited — что именно мешает карте держать частоту. «Нет» — "
            "ничего не ограничивает."
        )

        self._chart = LiveChart(window_s=300)
        self._chart.setMinimumHeight(220)

        chart_row = QHBoxLayout()
        chart_row.addWidget(QLabel("График (°C / % слева, МГц справа, окно 5 мин):"))
        chart_row.addStretch(1)

        hint = QLabel("Наведите на карточку — подсказка; клик по карточке — подробное окно.")
        hint.setObjectName("hintLabel")

        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(self._limits)
        lay.addWidget(hint)
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
        # Счётчик активности — накопленная занятость GPU в секундах: живой прогон
        # подтвердил, что в простое стоит (12 с по часам → +1 при первом рывке
        # частоты), растёт под нагрузкой. Сырые большие числа нечитаемы —
        # разделяем разряды пробелами. Это НЕ «время с загрузки драйвера».
        counter = snap.activity_counter
        self._activity.set_value(None if counter is None else f"{counter:,}".replace(",", " "))

        self._limits.setText(f"Ограничение: {snap.limit_summary()}")

        # На график кладём только полные снимки — частичные точки соврали бы картинку.
        gpu_temp = snap.gpu_temp_c
        gpu_power = snap.gpu_power_percent
        gpu_freq = snap.gpu_freq_mhz
        if gpu_temp is not None and gpu_power is not None and gpu_freq is not None:
            t = time.monotonic() - self._t0
            if self._t_last is not None and t - self._t_last > GAP_MAX_S:
                # Долгий перерыв (сон/занятый воркер): линия через паузу — ложь.
                self._chart.clear()
            self._chart.push(t, gpu_temp, gpu_power, gpu_freq)
            self._t_last = t


__all__ = ["MonitoringTab"]
