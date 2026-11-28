"""Чтение телеметрии Intel Arc (read-only, безопасно).

Отвечает на вопрос «в чём карта упёрлась прямо сейчас»: причины ограничения
(GpuPowerLimited и т.п.) + датчики (В, МГц, °C, % питания, Вт).
"""

from __future__ import annotations

import csv
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from arctuner.igcl import types as T
from arctuner.igcl.api import (
    CtlDeviceHandle,
    CtlEngineHandle,
    CtlMemHandle,
    DeviceInfo,
    IgclApi,
    IgclError,
    TelemetrySnapshot,
)
from arctuner.loadsource import SOURCE_IGCL, SOURCE_LABELS, current_source


def find_arc_adapter(api: IgclApi) -> tuple[CtlDeviceHandle, DeviceInfo] | None:
    """Ищет дискретную Arc (приоритет — B580, device_id 0xE20B). Среди
    перечисленных устройств возвращает handle и свойства первой дискретной Arc."""
    best: tuple[CtlDeviceHandle, DeviceInfo] | None = None
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


def read_snapshot(api: IgclApi, handle: CtlDeviceHandle) -> TelemetrySnapshot:
    """Снимает телеметрию. Read-only, безопасно."""
    return api.read_power_telemetry(handle)


class PowerMeter:
    """Считает среднюю мощность в ваттах дифференциацией счётчиков энергии (Дж).

    В структуре ctl_power_telemetry_t нет мгновенной строки «Вт» — есть только
    накопительные счётчики энергии (gpuEnergyCounter / vramEnergyCounter /
    totalCardEnergyCounter). Отношение ΔE/Δt за интервал опроса даёт честную
    среднюю мощность без «ручных» конверсий из процентов (проценты зависят от
    текущего лимита).

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
        self._vram: tuple[float, float] | None = None

    def enrich(self, snap: TelemetrySnapshot, t: float | None = None) -> TelemetrySnapshot:
        """Возвращает копию снимка с заполненными gpu/card/vram_power_w.

        Args:
            snap: снимок от read_power_telemetry (с сырьём *_energy_counter_j).
            t: метка времени (monotonic); по умолчанию — текущий момент. В тестах
                передаётся явно, в проде — всегда time.monotonic().

        Returns:
            Новый снимок (frozen dataclass — исходный не меняется).
        """
        now = time.monotonic() if t is None else t
        gpu_w, self._gpu = self._step(self._gpu, now, snap.gpu_energy_counter_j)
        card_w, self._card = self._step(self._card, now, snap.card_energy_counter_j)
        vram_w, self._vram = self._step(self._vram, now, snap.vram_energy_counter_j)
        return replace(snap, gpu_power_w=gpu_w, card_power_w=card_w, vram_power_w=vram_w)

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


class LoadMeter:
    """Считает загрузку ядра (%) и память (байты/%) — read-only.

    **Загрузка ядра**: два снимка ``ctlEngineGetActivity`` →
    ``%util = ΔactiveTime / Δtimestamp`` (формула из igcl_api.h). Первый снимок —
    база, % нет — так же, как у PowerMeter нет Вт на первом тике.

    **Медиа (%)**: та же формула по группе ``CTL_ENGINE_GROUP_MEDIA``
    (video decode/processing) — опциональная метрика (см.
    ``media_load_percent``), в CSV не пишется. У каждой группы свой счётчик
    и своя база дельты.

    **Память**: ``ctlMemoryGetState`` → ``used = size − free``,
    ``% = 100 · used / size`` — без дельты, доступна сразу с первого тика.

    Источник берётся из настройки (см. arctuner.loadsource); сейчас реализован
    только ``igcl`` — прочие источники честно отклоняются при создании, без
    тихого отката.

    Один экземпляр на один поток опроса (не потокобезопасен).
    """

    #: минимальный интервал (мкс) между снимками для честного % (аналог min_gap
    #: у PowerMeter): короче — шум квантования, базу не сдвигаем.
    _MIN_GAP_US = 100_000  # 0.1 с

    def __init__(
        self,
        api: IgclApi,
        handle: CtlDeviceHandle,
        source: str | None = None,
    ) -> None:
        src = source or current_source()
        if src != SOURCE_IGCL:
            raise NotImplementedError(
                f"Источник загрузки {src!r} "
                f"({SOURCE_LABELS.get(src, src)}) ещё не реализован — "
                f"используйте {SOURCE_IGCL!r}."
            )
        self._api = api
        loader = api.loader
        # RENDER (render+compute) — итоговая загрузка ядра. Замер на живой B580
        # (2026-11-07): RENDER совпал с независимым счётчиком Windows (PDH
        # «3D engine»: 6.3 % против 6–7 %), а GT вёл себя как усреднение по всей
        # группе (≈ Σзанятого/9): в игре с одним занятым 3D-движком GT давал
        # ≤11 % вместо ~100 %.
        # MEDIA (video decode/processing) — опциональная метрика «Медиа, %»
        # для мониторинга видеонагрузки; ищем обе группы за один проход.
        self._engine: CtlEngineHandle | None = None
        self._media: CtlEngineHandle | None = None
        if loader.has("ctlEnumEngineGroups") and loader.has("ctlEngineGetActivity"):
            for eng in api.enumerate_engine_groups(handle):
                if self._engine is not None and self._media is not None:
                    break  # обе группы найдены
                try:
                    group = api.get_engine_properties(eng)
                except IgclError:
                    continue  # группа не отдала свойства — пропускаем
                if group == T.CtlEngineGroup.RENDER and self._engine is None:
                    self._engine = eng
                elif group == T.CtlEngineGroup.MEDIA and self._media is None:
                    self._media = eng
        # Первый модуль памяти (на B580 их один).
        self._mem: CtlMemHandle | None = None
        if loader.has("ctlEnumMemoryModules") and loader.has("ctlMemoryGetState"):
            mods = api.enumerate_memory_modules(handle)
            if mods:
                self._mem = mods[0]
        # База дельты для % загрузки (первый снимок каждой группы).
        self._prev: tuple[int, int] | None = None
        self._media_prev: tuple[int, int] | None = None
        if self._engine is not None:
            act = api.read_engine_activity(self._engine)
            self._prev = (act.active_time_us, act.timestamp_us)
        if self._media is not None:
            act = api.read_engine_activity(self._media)
            self._media_prev = (act.active_time_us, act.timestamp_us)

    def enrich(self, snap: TelemetrySnapshot) -> TelemetrySnapshot:
        """Возвращает копию снимка с заполненными load % (RENDER/MEDIA) и памятью.

        Args:
            snap: снимок от read_power_telemetry.

        Returns:
            Новый снимок (frozen dataclass — исходный не меняется).
        """
        load: float | None = None
        if self._engine is not None:
            act = self._api.read_engine_activity(self._engine)
            load = self._step(act.active_time_us, act.timestamp_us)
        media: float | None = None
        if self._media is not None:
            act = self._api.read_engine_activity(self._media)
            media = self._step_media(act.active_time_us, act.timestamp_us)
        used: int | None = None
        total: int | None = None
        pct: float | None = None
        if self._mem is not None:
            st = self._api.read_memory_state(self._mem)
            used, total, pct = st.used, st.size, st.percent
        return replace(
            snap,
            gpu_load_percent=load,
            media_load_percent=media,
            vram_used_bytes=used,
            vram_total_bytes=total,
            vram_percent=pct,
        )

    def _step(self, active_us: int, ts_us: int) -> float | None:
        """Один шаг расчёта % загрузки ядра (RENDER): (%, новая база)."""
        value, self._prev = self._advance(self._prev, active_us, ts_us)
        return value

    def _step_media(self, active_us: int, ts_us: int) -> float | None:
        """Тот же шаг для группы MEDIA (video decode/processing)."""
        value, self._media_prev = self._advance(self._media_prev, active_us, ts_us)
        return value

    @classmethod
    def _advance(
        cls,
        prev: tuple[int, int] | None,
        active_us: int,
        ts_us: int,
    ) -> tuple[float | None, tuple[int, int]]:
        """Общий шаг дельты двух снимков: (%, новая база).

        База возвращается отдельно от %: у RENDER и MEDIA свои счётчики —
        каждая группа держит свою пару снимков, математика одна.
        """
        if prev is None:
            return None, (active_us, ts_us)
        pa, pt = prev
        d_active = active_us - pa
        d_ts = ts_us - pt
        if d_ts <= 0 or d_active < 0:
            return None, (active_us, ts_us)  # откат счётчика (рестарт драйвера)
        if d_ts < cls._MIN_GAP_US:
            return None, prev  # базу не сдвигаем — интервал накопится
        return max(0.0, min(100.0, 100.0 * d_active / d_ts)), (active_us, ts_us)


#: Колонки CSV телеметрии (стабильный порядок для анализа — не переставлять).
TELEMETRY_CSV_FIELDS: list[str] = [
    "timestamp",
    "gpu_freq_mhz",
    "gpu_voltage_v",
    "gpu_temp_c",
    "gpu_power_percent",
    "gpu_power_w",
    "card_power_w",
    "vram_power_w",
    "gpu_vr_temp_c",
    "vram_vr_temp_c",
    "sa_vr_temp_c",
    "vram_read_bandwidth_mbps",
    "vram_write_bandwidth_mbps",
    "gpu_over_voltage_percent",
    "vram_eff_freq_mbps",
    "vram_voltage_v",
    "vram_temp_c",
    "power_limited",
    "temperature_limited",
    "current_limited",
    "voltage_limited",
    "utilization_limited",
    "gpu_load_percent",
    "vram_used_bytes",
    "vram_total_bytes",
    "vram_percent",
    "fps",
]


def snapshot_csv_row(snap: TelemetrySnapshot, wall: float | None = None) -> dict[str, object]:
    """Строка CSV из снимка (ключи — ровно ``TELEMETRY_CSV_FIELDS``).

    Общая форма для CLI (``telemetry --csv``) и GUI-экспорта буфера реплея:
    одна колонка — один смысл, порядок стабилен.

    Args:
        snap: снимок телеметрии.
        wall: epoch-метка времени (по умолчанию — момент вызова).
    """
    if wall is None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(wall))
    return {
        "timestamp": stamp,
        "gpu_freq_mhz": snap.gpu_freq_mhz,
        "gpu_voltage_v": snap.gpu_voltage_v,
        "gpu_temp_c": snap.gpu_temp_c,
        "gpu_power_percent": snap.gpu_power_percent,
        "gpu_power_w": snap.gpu_power_w,
        "card_power_w": snap.card_power_w,
        "vram_power_w": snap.vram_power_w,
        "gpu_vr_temp_c": snap.gpu_vr_temp_c,
        "vram_vr_temp_c": snap.vram_vr_temp_c,
        "sa_vr_temp_c": snap.sa_vr_temp_c,
        "vram_read_bandwidth_mbps": snap.vram_read_bandwidth_mbps,
        "vram_write_bandwidth_mbps": snap.vram_write_bandwidth_mbps,
        "gpu_over_voltage_percent": snap.gpu_over_voltage_percent,
        "vram_eff_freq_mbps": snap.vram_eff_freq_mbps,
        "vram_voltage_v": snap.vram_voltage_v,
        "vram_temp_c": snap.vram_temp_c,
        "power_limited": snap.power_limited,
        "temperature_limited": snap.temperature_limited,
        "current_limited": snap.current_limited,
        "voltage_limited": snap.voltage_limited,
        "utilization_limited": snap.utilization_limited,
        "gpu_load_percent": snap.gpu_load_percent,
        "vram_used_bytes": snap.vram_used_bytes,
        "vram_total_bytes": snap.vram_total_bytes,
        "vram_percent": snap.vram_percent,
        "fps": snap.fps,
    }


def export_telemetry(path: Path | str, rows: Iterable[dict[str, object]]) -> int:
    """Дописывает строки телеметрии в CSV (формат ``telemetry --csv``).

    Заголовок пишется, если файла нет или он пуст: повторный вызов
    продолжает тот же файл (семантика ``--watch``), а батч из буфера
    реплея создаёт файл целиком.

    Args:
        path: файл назначения (каталог создаётся).
        rows: строки из :func:`snapshot_csv_row`.

    Returns:
        Число записанных строк (без заголовка).

    Raises:
        OSError: диск/права — исключение не глотается.
    """
    p = Path(path)
    write_header = not p.exists() or p.stat().st_size == 0
    p.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TELEMETRY_CSV_FIELDS)
        if write_header:
            w.writeheader()
        for row in rows:
            w.writerow(row)
            written += 1
    return written


__all__ = [
    "TELEMETRY_CSV_FIELDS",
    "LoadMeter",
    "PowerMeter",
    "export_telemetry",
    "find_arc_adapter",
    "read_snapshot",
    "snapshot_csv_row",
]
