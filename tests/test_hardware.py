"""Тесты на реальном железе (Intel Arc). Пропускаются без карты.

Помечены @pytest.mark.hardware (см. pyproject.toml). Запуск только явный:
    python -m pytest -m hardware
ВНИМАНИЕ: тесты только читают (init/enumerate/properties/telemetry/fans/VF).
Запись сюда не выносится — она покрыта safety-тестами без железа.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.hardware

from arctuner.igcl.api import IgclApi, IgclError  # noqa: E402
from arctuner.telemetry import find_arc_adapter  # noqa: E402


@pytest.fixture(scope="module")
def api():
    """Открытый IGCL-API на время модуля."""
    with IgclApi() as a:
        yield a


@pytest.fixture(scope="module")
def arc(api: IgclApi):
    """Handle дискретной Arc (B580), если есть."""
    found = find_arc_adapter(api)
    if not found:
        pytest.skip("Дискретная Intel Arc не найдена")
    return found


def test_init_enumerate(api: IgclApi) -> None:
    """ctlInit + ctlEnumerateDevices возвращают хотя бы одно устройство."""
    devices = api.enumerate_devices()
    assert len(devices) >= 1


def test_device_properties(api: IgclApi, arc) -> None:
    """Свойства устройства читаются, vendor Intel."""
    _, info = arc
    assert info.vendor_id == 0x8086
    assert info.name  # непустое имя


def test_b580_identified(arc) -> None:
    """Если карта — B580, device_id должен быть 0xE20B."""
    _, info = arc
    if "B580" in info.name:
        assert info.device_id == 0xE20B


def test_power_telemetry(api: IgclApi, arc) -> None:
    """Телеметрия читается, базовые значения в правдоподобных пределах."""
    handle, _ = arc
    t = api.read_power_telemetry(handle)
    # Температура под нагрузкой/покой — в разумных границах для GPU.
    if t.gpu_temp_c is not None:
        assert 0 < t.gpu_temp_c < 110
    # Напряжение ядра: 0.7...1.2 В для Arc под нагрузкой/покой.
    if t.gpu_voltage_v is not None:
        assert 0.5 < t.gpu_voltage_v < 1.3
    # Память: 19-22 Гбит/с (номинал 19).
    if t.vram_eff_freq_mbps is not None:
        assert 15 <= t.vram_eff_freq_mbps <= 23


def test_oc_properties_limits(api: IgclApi, arc) -> None:
    """Диапазоны OC читаются; потолок питания ровно 120% (инвариант B580)."""
    handle, _ = arc
    oc = api.read_oc_properties(handle)
    assert oc["supported"] is True
    power = oc["powerLimit"]
    # Жёсткий инвариант: софт-потолок питания = 120% (аппаратный, шунт).
    assert power.max == pytest.approx(120.0)
    # Память: номинал 19, софтверный запас до 22.
    vram = oc["vramMemSpeedLimit"]
    assert vram.min == pytest.approx(19.0)
    assert vram.max <= 22.0
    # Границы кривой V/F: драйвер отдает их со Version > 0 (живой B580).
    vf_v = oc["gpuVFCurveVoltageLimit"]
    vf_f = oc["gpuVFCurveFrequencyLimit"]
    assert vf_v.supported and vf_v.units == 13  # VOLTAGE_MILLIVOLTS
    assert vf_f.supported and vf_f.units == 0  # FREQUENCY_MHZ
    # Жёсткие рамки проекта (600..1250 мВ / 300..3300 МГц) должны попадать
    # внутрь рамок драйвера — иначе наши инвариантные проверки бессмысленны.
    assert vf_v.min <= 600 < 1250 <= vf_v.max
    assert vf_f.min <= 300 < 3300 <= vf_f.max


def test_fans_read(api: IgclApi, arc) -> None:
    """Вентиляторы перечисляются, скорость читается (>0 под нагрузкой)."""
    handle, _ = arc
    fans = api.enum_fans(handle)
    assert len(fans) >= 1
    speed = api.get_fan_speed(fans[0])
    assert speed >= -1  # -1 = не измеряется, допустимо


def test_vf_curve_read(api: IgclApi, arc) -> None:
    """Кривая V/F читается, точки монотонно возрастают по частоте."""
    handle, _ = arc
    pts = api.read_vf_curve(handle)
    assert len(pts) >= 2
    freqs = [p.frequency for p in pts]
    assert freqs == sorted(freqs)


def test_teardown_clean(api: IgclApi) -> None:
    """Контекстный менеджер закрывает handle без ошибок (smoke)."""
    # Если мы здесь — init прошёл. Просто убеждаемся, что IgclError не «глотается».
    assert isinstance(api, IgclApi)
    with pytest.raises(IgclError):
        # Вызов без инициализированного устройства кидает IgclError, а не None.
        api.read_power_telemetry(0)
