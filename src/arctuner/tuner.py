"""Установка настроек разгона Intel Arc (write — единственный опасный модуль).

Все записи проходят проверки: поддерживается ли пункт, не read-only ли, в допустимом
ли диапазоне min/max/step из IGCL. Модуль принципиально НЕ умеет ставить запрещённое
(лимит >120%, тайминги памяти, vBIOS) — это инварианты проекта (см. AGENTS.md).
"""

from __future__ import annotations

from arctuner.igcl.api import IgclApi, IgclHandle

# Жёсткие потолки, выше которых софт не должен ставить (см. docs/overclocking.md).
MAX_POWER_LIMIT_PERCENT = 120.0  # аппаратный лимит B580 ≈226 Вт
MAX_VRAM_SPEED_GBPS = 20.0  # номинал чипа SC20; выше — лотерея


class SafetyLimitError(ValueError):
    """Попытка выйти за безопасный/поддерживаемый диапазон."""


def _check_range(name: str, value: float, lo: float, hi: float) -> None:
    if not (lo <= value <= hi):
        raise SafetyLimitError(f"{name}={value} вне диапазона [{lo}, {hi}]")


def set_vram_speed(api: IgclApi, adapter: IgclHandle, gbps: float) -> None:
    """Ставит скорость памяти, Гбит/с. Безопасный потолок — 20 (номинал чипа SC20).

    Raises:
        SafetyLimitError: если значение выходит за поддерживаемый диапазон.
        NotImplementedError: до реализации записи в MVP.
    """
    _check_range("vram_speed", gbps, 19.0, MAX_VRAM_SPEED_GBPS)
    raise NotImplementedError("ctlOverclockVramMemSpeedLimitSetV2 — в ближайшем MVP.")


def set_power_limit(api: IgclApi, adapter: IgclHandle, percent: float) -> None:
    """Ставит лимит питания в %. Софтверный потолок B580 — 120%.

    Raises:
        SafetyLimitError: если >120% (аппаратный потолок, обход невозможен софтом).
        NotImplementedError: до реализации записи в MVP.
    """
    _check_range("power_limit", percent, 50.0, MAX_POWER_LIMIT_PERCENT)
    raise NotImplementedError("ctlOverclockPowerLimitSetV2 — в ближайшем MVP.")


def reset_to_default(api: IgclApi, adapter: IgclHandle) -> None:
    """Сброс всех OC-настроек к дефолту (план отката).

    Raises:
        NotImplementedError: до реализации записи в MVP.
    """
    raise NotImplementedError("ctlOverclockResetToDefault — в ближайшем MVP.")
