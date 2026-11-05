"""Read-only телеметрия процессора через psutil."""

from __future__ import annotations

import math
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class CpuSnapshot:
    """Снимок загрузки и частоты CPU; температура зависит от внешнего источника."""

    usage_percent: float | None
    per_core_usage_percent: tuple[float, ...]
    frequency_mhz: float | None


class CpuReadError(RuntimeError):
    """Ошибка получения доступных метрик CPU."""


class CpuSampler:
    """Сэмплирует общую/поточную загрузку и частоту CPU без записи настроек."""

    def __init__(self) -> None:
        self._primed = False

    def sample(self) -> CpuSnapshot:
        """Возвращает снимок; загрузка доступна начиная со второго вызова."""
        try:
            per_core = tuple(float(value) for value in psutil.cpu_percent(percpu=True))
            frequency = psutil.cpu_freq()
        except (psutil.Error, OSError, RuntimeError) as exc:
            raise CpuReadError(f"не удалось прочитать CPU-телеметрию: {exc}") from exc

        if not self._primed:
            self._primed = True
            usage = None
            per_core = ()
        else:
            valid = tuple(
                value for value in per_core if math.isfinite(value) and 0.0 <= value <= 100.0
            )
            per_core = valid
            usage = sum(valid) / len(valid) if valid else None

        frequency_mhz = None
        if frequency is not None and math.isfinite(frequency.current) and frequency.current > 0:
            frequency_mhz = float(frequency.current)
        return CpuSnapshot(usage, per_core, frequency_mhz)


__all__ = ["CpuReadError", "CpuSampler", "CpuSnapshot"]
