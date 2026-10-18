"""Чтение телеметрии Intel Arc (read-only, безопасно).

Отвечает на вопрос «в чём карта упёрлась прямо сейчас»: причины ограничения
(GpuPowerLimited и т.п.) + датчики (В, МГц, °C, % питания).
"""

from __future__ import annotations

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
