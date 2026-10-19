"""Тесты PowerMeter: расчёт ваттов из счётчиков энергии (ΔE/Δt), без железа.

Сценарии: первый тик (база), обычный шаг, слишком частые тики (база держится),
откат счётчика (перезагрузка драйвера), слишком редкий шаг (пропуск), счётчик
не поддерживается (None). Время передаётся явно — тесты детерминированы.
"""

from __future__ import annotations

import pytest

from arctuner.igcl.api import TelemetrySnapshot
from arctuner.telemetry import PowerMeter


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
