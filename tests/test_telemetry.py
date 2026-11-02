"""Тесты PowerMeter: расчёт ваттов из счётчиков энергии (ΔE/Δt), без железа.

Сценарии: первый тик (база), обычный шаг, слишком частые тики (база держится),
откат счётчика (перезагрузка драйвера), слишком редкий шаг (пропуск), счётчик
не поддерживается (None). Время передаётся явно — тесты детерминированы.
"""

from __future__ import annotations

import pytest

from arctuner.igcl.api import ExtendedDeviceInfo, MemoryState, TelemetrySnapshot
from arctuner.telemetry import LoadMeter, PowerMeter


def test_driver_version_str_decodes_all_four_parts() -> None:
    """Кодировка IGCL: (major<<48)|(minor<<32)|(build<<16)|revision.

    Регрессия: прежняя формула теряла major/minor и показывала «101.9150»
    вместо реальной версии драйвера 32.0.101.9150 (сырое 0x00200000006523BE,
    сверено с Win32_PnPSignedDriver на живой системе).
    """
    ext = ExtendedDeviceInfo(
        info=None,  # type: ignore[arg-type]  # для свойства версии info не нужен
        driver_version=0x00200000006523BE,
        firmware_version_major=1,
        firmware_version_minor=0,
    )
    assert ext.driver_version_str == "32.0.101.9150"


def _snap(
    gpu_j: float | None = None,
    card_j: float | None = None,
    vram_j: float | None = None,
) -> TelemetrySnapshot:
    """Минимальный снимок: нужны только счётчики энергии."""
    return TelemetrySnapshot(
        gpu_voltage_v=1.0,
        gpu_freq_mhz=2500.0,
        gpu_temp_c=55.0,
        gpu_power_percent=70.0,
        vram_voltage_v=1.35,
        vram_eff_freq_mbps=19.75,
        vram_temp_c=48.0,
        power_limited=False,
        temperature_limited=False,
        current_limited=False,
        voltage_limited=False,
        utilization_limited=False,
        activity_counter=0,
        gpu_energy_counter_j=gpu_j,
        card_energy_counter_j=card_j,
        vram_energy_counter_j=vram_j,
    )


def test_first_sample_is_baseline_only() -> None:
    """Первый снимок — база: ваттов нет, значения не выдумываем."""
    out = PowerMeter().enrich(_snap(gpu_j=1000.0), t=0.0)
    assert out.gpu_power_w is None
    assert out.card_power_w is None
    assert out.vram_power_w is None


def test_watts_from_energy_delta() -> None:
    """150 Дж за 1 с = 150 Вт; карточный счётчик считается тем же правилом."""
    meter = PowerMeter()
    meter.enrich(_snap(gpu_j=1000.0, card_j=5000.0), t=0.0)
    out = meter.enrich(_snap(gpu_j=1150.0, card_j=5200.0), t=1.0)
    assert out.gpu_power_w == pytest.approx(150.0)
    assert out.card_power_w == pytest.approx(200.0)


def test_fast_polling_accumulates_interval() -> None:
    """Тик чаще min_gap не двигает базу — интервал накапливается до осмысленного."""
    meter = PowerMeter(min_gap_s=0.5)
    meter.enrich(_snap(gpu_j=1000.0), t=0.0)
    assert meter.enrich(_snap(gpu_j=1050.0), t=0.1).gpu_power_w is None
    # База осталась на t=0: 200 Дж за 2 с от исходной точки = 100 Вт.
    out = meter.enrich(_snap(gpu_j=1200.0), t=2.0)
    assert out.gpu_power_w == pytest.approx(100.0)


def test_counter_rollback_rebaselines() -> None:
    """Откат счётчика (перезагрузка драйвера) — не даём отрицательных ватт."""
    meter = PowerMeter()
    meter.enrich(_snap(gpu_j=1000.0), t=0.0)
    assert meter.enrich(_snap(gpu_j=100.0), t=1.0).gpu_power_w is None  # сброс
    # После перебазы следующий корректный шаг считается от новой точки.
    out = meter.enrich(_snap(gpu_j=250.0), t=2.0)
    assert out.gpu_power_w == pytest.approx(150.0)


def test_long_gap_rebaselines() -> None:
    """Пауза дольше max_gap (сон) — старые данные не растягиваются на пропуск."""
    meter = PowerMeter(max_gap_s=30.0)
    meter.enrich(_snap(gpu_j=1000.0), t=0.0)
    assert meter.enrich(_snap(gpu_j=5000.0), t=120.0).gpu_power_w is None
    out = meter.enrich(_snap(gpu_j=5150.0), t=121.0)
    assert out.gpu_power_w == pytest.approx(150.0)


def test_unsupported_counter_stays_none() -> None:
    """Счётчик не поддерживается (None) — ватт нет и база не строится."""
    meter = PowerMeter()
    meter.enrich(_snap(gpu_j=None, card_j=1000.0), t=0.0)
    out = meter.enrich(_snap(gpu_j=None, card_j=1200.0), t=1.0)
    assert out.gpu_power_w is None
    assert out.card_power_w == pytest.approx(200.0)


def test_vram_watts_from_energy_delta() -> None:
    """Память считается тем же правилом: 12 Дж за 4 с = 3 Вт (3 канала VR)."""
    meter = PowerMeter()
    meter.enrich(_snap(vram_j=1000.0), t=0.0)
    out = meter.enrich(_snap(vram_j=1012.0), t=4.0)
    assert out.vram_power_w == pytest.approx(3.0)


def test_new_fields_default_none() -> None:
    """VR-температуры/bandwidth/надбавка — None по умолчанию: api не выдумывает."""
    snap = _snap()
    assert snap.gpu_vr_temp_c is None
    assert snap.vram_vr_temp_c is None
    assert snap.sa_vr_temp_c is None
    assert snap.vram_read_bandwidth_mbps is None
    assert snap.vram_write_bandwidth_mbps is None
    assert snap.gpu_over_voltage_percent is None
    assert snap.vram_energy_counter_j is None
    assert snap.vram_power_w is None


def test_snapshot_watts_default_none() -> None:
    """Слой api не считает сам: ватты по умолчанию None."""
    assert _snap().gpu_power_w is None
    assert _snap().card_power_w is None


# --- LoadMeter: % загрузки ядра из двух снимков ctlEngineGetActivity ---


def _bare_load_meter() -> LoadMeter:
    """LoadMeter без железа: __init__ требует api/handle, здесь они не нужны.

    Тестируется только математика _step (как у PowerMeter — время передаётся
    явно), поэтому объект создаётся мимо конструктора.
    """
    meter = LoadMeter.__new__(LoadMeter)
    meter._prev = None
    return meter


def test_load_first_sample_is_baseline_only() -> None:
    """Первый снимок — база: % нет (не выдумываем)."""
    meter = _bare_load_meter()
    assert meter._step(active_us=1000, ts_us=10_000) is None
    assert meter._prev == (1000, 10_000)


def test_load_percent_from_deltas() -> None:
    """Формула igcl_api.h: %util = ΔactiveTime / Δtimestamp."""
    meter = _bare_load_meter()
    meter._step(active_us=1000, ts_us=10_000)
    # 50_000 мкс активности за 100_000 мкс = 50 %.
    assert meter._step(active_us=51_000, ts_us=110_000) == pytest.approx(50.0)


def test_load_fast_polling_accumulates_interval() -> None:
    """Снимок чаще min_gap не двигает базу — интервал накапливается."""
    meter = _bare_load_meter()
    meter._step(active_us=0, ts_us=0)
    # 5 мс активности за 10 мс (100 %) — но интервал 50 мс < min_gap (100 мс).
    assert meter._step(active_us=5_000, ts_us=50_000) is None
    # База осталась на 0: 55_000 активности за 150_000 мкс от исходной точки.
    out = meter._step(active_us=55_000, ts_us=150_000)
    assert out == pytest.approx(100.0 * 55_000 / 150_000)


def test_load_counter_rollback_rebaselines() -> None:
    """Откат счётчика (рестарт драйвера) — % нет, база переустанавливается."""
    meter = _bare_load_meter()
    meter._step(active_us=100_000, ts_us=100_000)
    assert meter._step(active_us=500, ts_us=200_000) is None
    out = meter._step(active_us=55_500, ts_us=300_000)
    assert out == pytest.approx(55.0)


def test_load_zero_delta_returns_none() -> None:
    """Нулевой/отрицательный интервал (ts не растёт) — % нет."""
    meter = _bare_load_meter()
    meter._step(active_us=1000, ts_us=10_000)
    assert meter._step(active_us=2000, ts_us=10_000) is None
    assert meter._step(active_us=3000, ts_us=9_000) is None


def test_load_percent_clamped_to_0_100() -> None:
    """Шум квантования не даёт выйти за 0..100 %."""
    meter = _bare_load_meter()
    meter._step(active_us=0, ts_us=0)
    # Δactive в 3 раза больше Δts — аномалия → 100 %, а не 300 %.
    assert meter._step(active_us=300_000, ts_us=100_000) == 100.0


def test_load_rejects_unimplemented_source() -> None:
    """pdh/dxgi ещё не реализованы — честное исключение, без тихого отката."""

    class _FakeApi:
        loader = None

    with pytest.raises(NotImplementedError):
        LoadMeter(_FakeApi(), None, source="dxgi")  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        LoadMeter(_FakeApi(), None, source="pdh")  # type: ignore[arg-type]


# --- MemoryState: used/percent из ctlMemoryGetState ---


def test_memory_state_used_and_percent() -> None:
    """Формула igcl_api.h: used = size − free, % = 100 · used / size."""
    st = MemoryState(free=4_000_000_000, size=12_000_000_000)
    assert st.used == 8_000_000_000
    assert st.percent == pytest.approx(66.666, abs=0.01)


def test_memory_state_empty_size_has_no_percent() -> None:
    """size == 0 (неизвестно) — % нет, деления на ноль избегаем."""
    st = MemoryState(free=0, size=0)
    assert st.used == 0
    assert st.percent is None


def test_memory_state_free_exceeding_size_clamps_used() -> None:
    """Аномалия free > size — used не уходит в минус (max(0, …))."""
    st = MemoryState(free=200, size=100)
    assert st.used == 0
    assert st.percent == pytest.approx(0.0)


def test_new_snapshot_load_fields_default_none() -> None:
    """Load-поля снимка — None по умолчанию: api их не заполняет."""
    snap = _snap()
    assert snap.gpu_load_percent is None
    assert snap.vram_used_bytes is None
    assert snap.vram_total_bytes is None
    assert snap.vram_percent is None
