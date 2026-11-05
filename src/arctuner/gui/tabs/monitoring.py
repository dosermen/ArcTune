"""Вкладка «Мониторинг»: датчики + живой график (read-only)."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.charts import LiveChart
from arctuner.gui.widgets import MetricCard
from arctuner.i18n import tr
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
    "load": "Загрузка ядра в % — доля времени, когда GPU реально работал за период "
    "опроса (формула igcl_api.h: ΔactiveTime/Δtimestamp по группе GT, все движки "
    "вместе). Появляется со 2-го тика: первый снимок — база для расчёта. Это "
    "занятость самого GPU, не процессора.",
    "vram_used": "Занято памяти: использовано / доступно (ГиБ) из ctlMemoryGetState "
    "(used = size − free). Процент — при наведении. «Доступно» чуть меньше "
    "физических 12 ГиБ: драйвер резервирует часть под себя.",
    "gpu_vr": "Температура фаз питания (VRM) ядра — мосфеты на плате. Греются от тока, а "
    "не от вычислений: перегрев фаз по кристаллу не виден.",
    "vram_vr": "Температура фаз питания памяти. В простое часто самая горячая из пяти "
    "(на живой B580 ~64 °C при 45–48 °C кристалла).",
    "sa_vr": "Температура фаз питания SA (System Agent — домен контроллера/обвязки "
    "кристалла). Обычно наименее нагруженная.",
}

_NOTES_EN: dict[str, str] = {
    "freq": "Actual GPU core frequency. Under throttling (Power/Temperature Limited), it falls below the requested value — compare with the limit indicator.",
    "voltage": "GPU core voltage. Positive offset is set on the Overclock tab; fine tuning is done with the V/F curve.",
    "temp": "GPU die temperature — the main heat sensor. The temperature limit and throttling use it. Project guideline: stay below 80 °C under load.",
    "power": "Power: % of the current power limit; watts are averaged from energy counters and appear after the second poll.",
    "vram_freq": "Actual effective GDDR6 memory speed (B580 nominal: 20 Gbps).",
    "vram_voltage": "Memory voltage. Fixed at 1.35 V on B580; the driver does not allow changing it.",
    "vram_temp": "GDDR6 chip temperature (6× Samsung SC20). Memory overheating can cause errors, artifacts, and crashes.",
    "vram_power": "Memory power in watts, calculated from the vramEnergyCounter (read-only energy counter).",
    "activity": "Accumulated GPU active time in seconds. It grows under load and may stand still at idle — that is normal.",
    "load": "GPU core utilization: the share of time the GPU was active during the polling interval (ΔactiveTime/Δtimestamp for the GT group). Available after the second sample.",
    "vram_used": "VRAM used / available (GiB), from ctlMemoryGetState (used = size − free). The available amount may be below physical 12 GiB because the driver reserves memory.",
    "gpu_vr": "GPU VRM phase temperature. The MOSFETs heat from current; die temperature does not reveal their temperature.",
    "vram_vr": "Memory VRM phase temperature. At idle this can be the hottest of the VR sensors.",
    "sa_vr": "System Agent VRM phase temperature.",
}


class MonitoringTab(QWidget):
    """Показывает текущую телеметрию и скроллящийся график последних минут."""

    #: экспорт буфера телеметрии (путь к CSV) — воркер пишет replay-буфер
    export_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._t0 = time.monotonic()
        self._t_last: float | None = None

        def note(key: str) -> str:
            return tr(_NOTES[key], _NOTES_EN[key])

        self._freq = MetricCard(
            tr("Ядро", "Core"), tr("МГц", "MHz"), accent="freq", note=note("freq")
        )
        self._voltage = MetricCard(tr("Напряжение", "Voltage"), tr("В", "V"), note=note("voltage"))
        self._temp = MetricCard(
            tr("Температура", "Temperature"), "°C", accent="temp", note=note("temp")
        )
        self._power = MetricCard(tr("Питание", "Power"), "%", accent="power", note=note("power"))

        self._vram_freq = MetricCard(
            tr("Память", "Memory"), tr("Гбит/с", "Gbps"), accent="freq", note=note("vram_freq")
        )
        self._vram_voltage = MetricCard(
            tr("Память, V", "Memory, V"), tr("В", "V"), note=note("vram_voltage")
        )
        self._vram_temp = MetricCard(
            tr("Память, T", "Memory, Temp"), "°C", accent="temp", note=note("vram_temp")
        )
        self._vram_power = MetricCard(
            tr("Память, Вт", "Memory, W"), tr("Вт", "W"), accent="power", note=note("vram_power")
        )

        self._activity = MetricCard(
            tr("Активность", "Activity"), tr("с", "s"), note=note("activity")
        )
        self._gpu_vr = MetricCard(
            tr("VR ядра", "Core VRM"), "°C", accent="temp", note=note("gpu_vr")
        )
        self._vram_vr = MetricCard(
            tr("VR памяти", "Memory VRM"), "°C", accent="temp", note=note("vram_vr")
        )
        self._sa_vr = MetricCard("VR SA", "°C", accent="temp", note=note("sa_vr"))

        self._load = MetricCard(tr("Загрузка", "GPU Load"), "%", accent="load", note=note("load"))
        self._vram_used = MetricCard(
            tr("Память занято", "VRAM Used"),
            tr("ГиБ", "GiB"),
            accent="vram",
            note=note("vram_used"),
        )

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
        for col, card in enumerate((self._load, self._vram_used)):
            grid.addWidget(card, 3, col)

        self._limits = QLabel(tr("Ограничение: —", "Limit: —"))
        self._limits.setObjectName("limitsLabel")
        self._limits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._limits.setToolTip(
            tr(
                "Активный лимитер прямо сейчас: Power / Temperature / Current / Voltage / "
                "Utilization Limited — что именно мешает карте держать частоту. «Нет» — "
                "ничего не ограничивает.",
                "Active limiter right now: Power / Temperature / Current / Voltage / "
                "Utilization Limited — what is preventing the GPU from sustaining its clock. "
                "“None” means no active limit.",
            )
        )

        self._chart = LiveChart(window_s=300)

        self._export_btn = QPushButton(tr("Экспорт CSV…", "Export CSV…"))
        self._export_btn.setToolTip(
            tr(
                "Сохранить буфер наблюдения (последние ~10 минут телеметрии, тот же "
                "формат, что arctune telemetry --csv) в файл.",
                "Save the observation buffer (about the last 10 minutes of telemetry) "
                "to a CSV file using the same format as arctune telemetry --csv.",
            )
        )
        self._export_btn.clicked.connect(self._on_export)

        chart_row = QHBoxLayout()
        chart_row.addWidget(
            QLabel(
                tr(
                    "График (слева °C / %, справа МГц, окно 5 мин; % — мощность, "
                    "загрузка ядра, память):",
                    "Chart (left: °C / %, right: MHz, 5-minute window; %: power, "
                    "GPU load, memory):",
                )
            )
        )
        chart_row.addStretch(1)
        chart_row.addWidget(self._export_btn)

        hint = QLabel(
            tr(
                "Наведите на карточку — подсказка; клик по карточке — подробное окно.",
                "Hover over a card for a tooltip; click it to open the full explanation.",
            )
        )
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
            self._power.set_value(snap.gpu_power_w, digits=0, unit=tr("Вт", "W"), tooltip=pct)
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

        # Загрузка ядра и занятость памяти — LoadMeter (источник igcl): % ядра
        # появляется со 2-го тика (первый снимок — база), память — сразу.
        self._load.set_value(snap.gpu_load_percent)
        if snap.vram_used_bytes is not None and snap.vram_total_bytes:
            used_gib = snap.vram_used_bytes / 1024**3
            total_gib = snap.vram_total_bytes / 1024**3
            pct = f"{snap.vram_percent:.0f} %" if snap.vram_percent is not None else ""
            self._vram_used.set_value(
                f"{used_gib:.2f} / {total_gib:.2f}",
                unit=tr("ГиБ", "GiB"),
                tooltip=pct,
            )
        else:
            self._vram_used.set_value(None)

        summary = snap.limit_summary()
        if summary == "нет (запас есть)":
            summary = tr(summary, "None (headroom available)")
        self._limits.setText(f"{tr('Ограничение', 'Limit')}: {summary}")

        # На график кладём только полные снимки — частичные точки соврали бы картинку.
        # load/vram (% из LoadMeter) могут быть None — LiveChart их пропускает.
        gpu_temp = snap.gpu_temp_c
        gpu_power = snap.gpu_power_percent
        gpu_freq = snap.gpu_freq_mhz
        if gpu_temp is not None and gpu_power is not None and gpu_freq is not None:
            t = time.monotonic() - self._t0
            if self._t_last is not None and t - self._t_last > GAP_MAX_S:
                # Долгий перерыв (сон/занятый воркер): линия через паузу — ложь.
                self._chart.clear()
            self._chart.push(
                t, gpu_temp, gpu_power, gpu_freq, snap.gpu_load_percent, snap.vram_percent
            )
            self._t_last = t

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Экспорт телеметрии", "Export telemetry"), "telemetry.csv", "CSV (*.csv)"
        )
        if path:
            self.export_requested.emit(path)


__all__ = ["MonitoringTab"]
