"""Чтение телеметрии Intel Arc (read-only, безопасно).

Отвечает на вопрос «в чём карта упёрлась прямо сейчас»: причины ограничения
(GpuPowerLimited и т.п.) + датчики (В, МГц, °C, % питания, Вт).
"""

from __future__ import annotations

import time
from dataclasses import replace

from arctuner.igcl.api import DeviceInfo, IgclApi, TelemetrySnapshot


def find_arc_adapter(api: IgclApi) -> tuple[object, DeviceInfo] | None:
    """Ищет дискретную Arc (приоритет — B580, device_id 0xE20B). Среди
    перечисленных устройств возвращает handle и свойства первой дискретной Arc."""
    best: tuple[object, DeviceInfo] | None = None
    for handle in api.enumerate_devices():
        info = api.get_device_properties(handle)
        if info.is_integrated:
            continue
        # Дискретная Arc: vendor Intel (0x8086), не встроенная.
        if info.vendor_id == 0x8086:
            if info.is_arc_b580:
                return handle, info
            if best is None:
                best = (handle, info)
    return best


def read_snapshot(api: IgclApi, handle) -> TelemetrySnapshot:
    """Снимает телеметрию. Read-only, безопасно."""
    return api.read_power_telemetry(handle)


class PowerMeter:
    """Считает среднюю мощность в ваттах дифференциацией счётчиков энергии (Дж).

    В структуре ctl_power_telemetry_t нет мгновенной строки «Вт» — есть только
    накопительные счётчики энергии (gpuEnergyCounter / totalCardEnergyCounter).
    Отношение ΔE/Δt за интервал опроса даёт честную среднюю мощность без
    «ручных» конверсий из процентов (проценты зависят от текущего лимита).

    Поведение:
      - первый снимок — базовая точка, ватт ещё нет (None);
      - слишком частые вызовы (dt < min_gap) не сдвигают базу — интервал
        накапливается до осмысленного;
      - пропуск дольше max_gap (сон/подвисание) и откат счётчика (перезагрузка
        драйвера) → ватт нет, база переставляется на текущий снимок.

    Один экземпляр на один поток опроса (не потокобезопасен).
    """

    def __init__(self, min_gap_s: float = 0.2, max_gap_s: float = 30.0) -> None:
        self._min_gap_s = min_gap_s
        self._max_gap_s = max_gap_s
        # (t_monotonic, joules) последней базы расчёта на каждый счётчик
        self._gpu: tuple[float, float] | None = None
        self._card: tuple[float, float] | None = None

    def enrich(self, snap: TelemetrySnapshot, t: float | None = None) -> TelemetrySnapshot:
        """Возвращает копию снимка с заполненными gpu_power_w / card_power_w.

        Args:
            snap: снимок от read_power_telemetry (с сырьём gpu/card_energy_counter_j).
            t: метка времени (monotonic); по умолчанию — текущий момент. В тестах
                передаётся явно, в проде — всегда time.monotonic().

        Returns:
            Новый снимок (frozen dataclass — исходный не меняется).
        """
        now = time.monotonic() if t is None else t
        gpu_w, self._gpu = self._step(self._gpu, now, snap.gpu_energy_counter_j)
        card_w, self._card = self._step(self._card, now, snap.card_energy_counter_j)
        return replace(snap, gpu_power_w=gpu_w, card_power_w=card_w)

    def _step(
        self,
        state: tuple[float, float] | None,
        now: float,
        energy_j: float | None,
    ) -> tuple[float | None, tuple[float, float] | None]:
        """Один шаг расчёта: (Вт за интервал, новая база)."""
        if energy_j is None:
            return None, state  # счётчик не поддерживается — базы нет
        if state is None:
            return None, (now, energy_j)  # первый снимок — только база
        t0, e0 = state
        dt = now - t0
        if dt < self._min_gap_s:
            return None, state  # базу не сдвигаем — интервал накопится
        delta_j = energy_j - e0
        if delta_j < 0 or dt > self._max_gap_s:
            return None, (now, energy_j)  # сброс счётчика / пропуск — перебаза
        return delta_j / dt, (now, energy_j)


__all__ = ["PowerMeter", "find_arc_adapter", "read_snapshot"]
