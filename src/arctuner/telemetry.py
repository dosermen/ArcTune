"""Чтение телеметрии Intel Arc (read-only, безопасно).

Отвечает на вопрос «в чём карта упёрлась прямо сейчас»: причины ограничения
(GpuPowerLimited и т.п.) + датчики (ватты, °C, частоты, напряжения).
"""

from __future__ import annotations

from dataclasses import dataclass

from arctuner.igcl.api import IgclApi, IgclHandle


@dataclass(frozen=True)
class LimitReasons:
    """Причины ограничения производительности в данный момент."""

    gpu_power_limited: bool = False
    gpu_temperature_limited: bool = False
    gpu_current_limited: bool = False
    gpu_voltage_limited: bool = False
    gpu_utilization_limited: bool = False

    def any_limited(self) -> bool:
        """Есть ли хоть одно ограничение — то есть упёрлась ли карта."""
        return any(
            (
                self.gpu_power_limited,
                self.gpu_temperature_limited,
                self.gpu_current_limited,
                self.gpu_voltage_limited,
                self.gpu_utilization_limited,
            )
        )


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Снимок датчиков + причин ограничения. TODO: заполняется из ctlPowerTelemetryGet."""

    reasons: LimitReasons
    gpu_voltage_v: float | None = None
    gpu_freq_mhz: float | None = None
    gpu_temp_c: float | None = None
    vram_voltage_v: float | None = None
    vram_eff_freq_mhz: float | None = None
    vram_temp_c: float | None = None
    board_power_w: float | None = None


def read_snapshot(api: IgclApi, adapter: IgclHandle) -> TelemetrySnapshot:
    """Снимает телеметрию. Read-only.

    TODO: реализуется через ctlPowerTelemetryGet после enumerate_devices.

    Raises:
        NotImplementedError: до реализации MVP.
    """
    raise NotImplementedError("Следующий шаг MVP: ctlPowerTelemetryGet (см. roadmap).")
