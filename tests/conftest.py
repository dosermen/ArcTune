"""Общие фикстуры для тестов.

Убирает дублирование `_info()`, `_snap()`, `_FanProps` — ~40 тестов
используют эти функции/классы для создания фейковых данных.
"""

from __future__ import annotations

import pytest

from arctuner import i18n
from arctuner.gui.context import FanInfo
from arctuner.igcl.api import OcControlInfo, TelemetrySnapshot


@pytest.fixture(autouse=True)
def _pin_language_ru() -> None:
    """Изолирует язык: каждый тест стартует с русского (язык по умолчанию).

    tr() кэширует язык на уровне модуля, а на машине разработчика может быть
    сохранён английский (файл ~/.arctune/language) или выставлен ARCTUNE_LANG.
    Без фиксации GUI/CLI-тесты с русскими assert'ами падали бы. Тесты самой
    локализации переключают язык явно внутри теста — после этой фикстуры.
    """
    i18n.set_language(i18n.LANG_RU)


def _info(**kw: object) -> OcControlInfo:
    """Создать OcControlInfo с базовыми значениями + переопределения.

    Args:
        **kw: поля OcControlInfo для переопределения
            (supported, relative, units, min, max, step, default).

    Returns:
        OcControlInfo с заполненными полями.
    """
    base: dict = {
        "supported": True,
        "relative": False,
        "units": 0,
        "min": 0.0,
        "max": 100.0,
        "step": 1.0,
        "default": 0.0,
    }
    base.update(kw)
    return OcControlInfo(**base)  # type: ignore[return-value]


def _snap(
    gpu_j: float | None = 1000.0,
    card_j: float | None = 5000.0,
    vram_j: float | None = 100.0,
    activity: int = 42,
    tdp_pct: float = 70.0,
) -> TelemetrySnapshot:
    """Синтетический снимок телеметрии.

    Все поля — дефолтные, кроме счётчиков энергии (обязательны для
    PowerMeter и IncidentReplay).

    Args:
        gpu_j: счётчик GPU (джоули).
        card_j: счётчик карты (джоули).
        vram_j: счётчик VRAM (джоули).
        activity: счётчик активности.
        tdp_pct: % от TDP (заменяет gpu_power_percent).

    Returns:
        TelemetrySnapshot для тестов.
    """
    return TelemetrySnapshot(
        gpu_voltage_v=1.0,
        gpu_freq_mhz=2500.0,
        gpu_temp_c=55.0,
        gpu_power_percent=tdp_pct,
        vram_voltage_v=1.35,
        vram_eff_freq_mbps=19.75,
        vram_temp_c=48.0,
        power_limited=False,
        temperature_limited=False,
        current_limited=False,
        voltage_limited=False,
        utilization_limited=False,
        activity_counter=activity,
        gpu_energy_counter_j=gpu_j,
        card_energy_counter_j=card_j,
        vram_energy_counter_j=vram_j,
    )


class _FanProps:
    """Мини-дублёр CtlFanProperties для тестов.

    ctypes-структура тяжела для заготовки, поэтому этот класс
    имитирует нужные поля.
    """

    def __init__(self, maxRPM: int, canControl: bool) -> None:  # noqa: N803 — имена IGCL (C)
        self.maxRPM = maxRPM
        self.canControl = canControl
        self.supportedModes = 2  # только FIXED
        self.supportedUnits = 1  # только RPM


def _fan_info(index: int, rpm: int, max_rpm: int = 3000, can_control: bool = True) -> FanInfo:
    """Создать FanInfo для тестов вкладки вентиляторов.

    Args:
        index: индекс вентилятора.
        rpm: текущий RPM.
        max_rpm: максимальный RPM из Props.
        can_control: может ли контроллер управлять.

    Returns:
        FanInfo для передачи в tab.set_fans().
    """
    return FanInfo(
        index=index,
        rpm=rpm,
        props=_FanProps(maxRPM=max_rpm, canControl=can_control),
    )
