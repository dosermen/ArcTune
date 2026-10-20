"""Установка настроек разгона Intel Arc (write — единственный опасный модуль).

Каждая запись проходит ДВУХУРОННЕВУЮ проверку:
  1. Жёсткие инварианты проекта (AGENTS.md) — ниже софт не ставит никогда.
  2. Диапазон min/max/step/supported, возвращённый самим IGCL из
     ctlOverclockGetProperties — нельзя выйти за то, что драйвер объявил.

Модуль принципиально НЕ умеет ставить запрещённое: лимит >120%, тайминги
памяти (страпы), vBIOS. Read-only операции живут в telemetry.py.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from arctuner.igcl import types as T
from arctuner.igcl.api import IgclApi, IgclError, OcControlInfo

# Жёсткие потолки (см. docs/overclocking.md, docs/safety.md).
MAX_POWER_LIMIT_PERCENT = 120.0  # аппаратный лимит B580 ≈226 Вт (шунт)
MAX_VRAM_SPEED_GBPS = 20.0  # номинал чипа Samsung SC20; выше — только явным решением


class SafetyLimitError(ValueError):
    """Попытка выйти за безопасный/поддерживаемый диапазон."""


@dataclass(frozen=True)
class WriteReport:
    """Отчёт о проделанной записи (для лога/отката)."""

    field: str
    old: float | None
    new: float | None
    note: str = ""


def _check_hard_limit(name: str, value: float, lo: float, hi: float) -> None:
    """Уровень 1: жёсткий инвариант проекта (не зависит от IGCL)."""
    if not (lo <= value <= hi):
        raise SafetyLimitError(
            f"{name}={value} выходит за жёсткий предел [{lo}, {hi}] — "
            f"инвариант проекта, обход невозможен"
        )


def _check_igcl_range(name: str, value: float, info: OcControlInfo) -> None:
    """Уровень 2: диапазон/supported из ctlOverclockGetProperties."""
    if not info.supported:
        raise SafetyLimitError(f"{name} не поддерживается на этом устройстве/драйвере")
    if not (info.min <= value <= info.max):
        raise SafetyLimitError(
            f"{name}={value} вне поддерживаемого диапазона [{info.min}, {info.max}] (IGCL)"
        )
    if info.step > 0:
        # Кратность шагу относительно min (с допуском на float).
        steps = round((value - info.min) / info.step)
        if abs((info.min + steps * info.step) - value) > 1e-6:
            raise SafetyLimitError(
                f"{name}={value} не кратен шагу {info.step} от min={info.min} (IGCL)"
            )


def _oc(api: IgclApi, handle) -> dict[str, OcControlInfo | bool]:
    """Актуальные диапазоны OC — свежий запрос на каждую запись."""
    return api.read_oc_properties(handle)


def _ensure_waiver(api: IgclApi, handle) -> None:
    """ctlOverclockWaiverSet — обязателен перед записью напряжения, отрицательного
    смещения частоты и кривой V/F (иначе драйвер вернёт WAIVER_NOT_SET, 0x44000008).

    Согласие пользователя — это подтверждение записи в CLI/GUI (Intel требует
    «хотя бы один раз явно предупредить и получить согласие»).
    """
    api.set_overclock_waiver(handle)


def set_power_limit(api: IgclApi, handle, percent: float) -> WriteReport:
    """Ставит лимит питания в %. Потолок B580 — 120% (аппаратный).

    Raises:
        SafetyLimitError: вне [50, 120] или вне диапазона IGCL.
        IgclError: ошибка вызова драйвера.
    """
    _check_hard_limit("power_limit", percent, 50.0, MAX_POWER_LIMIT_PERCENT)
    info = _oc(api, handle)["powerLimit"]
    assert isinstance(info, OcControlInfo)
    _check_igcl_range("power_limit", percent, info)
    old = _read_current(api, handle, "powerLimit")  # фактическое «было», не default
    api.set_power_limit(handle, percent)
    return WriteReport("powerLimit", old, percent)


def set_gpu_freq_offset(api: IgclApi, handle, mhz: float) -> WriteReport:
    """Ставит смещение частоты ядра, МГц (типично −300…+1000).

    Отрицательное смещение требует waiver (Intel: может поднять напряжение
    выше заводских рамок) — вызывается автоматически перед записью.

    Raises:
        SafetyLimitError: вне диапазона IGCL.
        IgclError: ошибка вызова драйвера.
    """
    info = _oc(api, handle)["gpuFrequencyOffset"]
    assert isinstance(info, OcControlInfo)
    _check_igcl_range("gpu_freq_offset", mhz, info)
    if mhz < 0:
        _ensure_waiver(api, handle)
    old = _read_current(api, handle, "gpuFrequencyOffset")
    api.set_gpu_freq_offset(handle, mhz)
    return WriteReport("gpuFrequencyOffset", old, mhz)


def set_gpu_voltage_offset(api: IgclApi, handle, percent: float) -> WriteReport:
    """Ставит смещение напряжения ядра в % относительно текущего (0…100 — только ПЛЮС).

    B580: отрицательный диапазон драйвером не декларируется (min=0), а легаси-
    функция в ±мВ отвечает DEPRECATED_API (0x44000009) — undervolt на B580
    делается только через кривую V/F. Повышение напряжения — теплово/
    деградационно рискованно; вызывающий несёт ответственность за значение.

    Перед записью вызывается waiver — без него драйвер возвращает
    WAIVER_NOT_SET (0x44000008); это и была «ошибка при изменении».

    Raises:
        SafetyLimitError: вне диапазона IGCL.
        IgclError: ошибка вызова драйвера.
    """
    info = _oc(api, handle)["gpuVoltageOffset"]
    assert isinstance(info, OcControlInfo)
    _check_igcl_range("gpu_voltage_offset", percent, info)
    _ensure_waiver(api, handle)
    old = _read_current(api, handle, "gpuVoltageOffset")
    api.set_gpu_voltage_offset(handle, percent)
    return WriteReport("gpuVoltageOffset", old, percent)


def set_temperature_limit(api: IgclApi, handle, celsius: float) -> WriteReport:
    """Ставит лимит температуры, °C (IGCL: 60…100).

    Raises:
        SafetyLimitError: вне диапазона IGCL.
        IgclError: ошибка вызова драйвера.
    """
    info = _oc(api, handle)["temperatureLimit"]
    assert isinstance(info, OcControlInfo)
    _check_igcl_range("temperature_limit", celsius, info)
    old = _read_current(api, handle, "temperatureLimit")
    api.set_temperature_limit(handle, celsius)
    return WriteReport("temperatureLimit", old, celsius)


def set_vram_speed(
    api: IgclApi, handle, gbps: float, *, allow_beyond_nominal: bool = False
) -> WriteReport:
    """Ставит скорость памяти, Гбит/с.

    Безопасный потолок — 20 (номинал чипа SC20). Выше 20 драйвер формально
    разрешает до 22, но это разгон сверх номинала чипа — поэтому требует
    явного флага allow_beyond_nominal=True (согласованное решение пользователя).

    Raises:
        SafetyLimitError: вне [19, 20] (или [19, 22] с флагом), либо вне IGCL.
        IgclError: ошибка вызова драйвера.
    """
    hi = 22.0 if allow_beyond_nominal else MAX_VRAM_SPEED_GBPS
    _check_hard_limit("vram_speed", gbps, 19.0, hi)
    info = _oc(api, handle)["vramMemSpeedLimit"]
    assert isinstance(info, OcControlInfo)
    _check_igcl_range("vram_speed", gbps, info)
    old = _read_current(api, handle, "vramMemSpeedLimit")
    api.set_vram_mem_speed(handle, gbps)
    note = "сверх номинала SC20 (20) — явное согласие" if gbps > MAX_VRAM_SPEED_GBPS else ""
    return WriteReport("vramMemSpeedLimit", old, gbps, note)


def reset_to_default(api: IgclApi, handle) -> WriteReport:
    """Сброс всех OC-настроек к дефолту (основной план отката).

    Не проходит проверки диапазонов — это и есть возврат к безопасному
    состоянию. Единственная запись, разрешённая без выбора значения.

    Raises:
        IgclError: ошибка вызова драйвера.
    """
    api.reset_to_default(handle)
    return WriteReport("all", None, None, "сброс к дефолту")


#: OC-поле -> V2 Get-функция IGCL (чтение фактического значения).
_CURRENT_GETTERS: dict[str, str] = {
    "gpuFrequencyOffset": "ctlOverclockGpuFrequencyOffsetGetV2",
    "powerLimit": "ctlOverclockPowerLimitGetV2",
    "temperatureLimit": "ctlOverclockTemperatureLimitGetV2",
    "vramMemSpeedLimit": "ctlOverclockVramMemSpeedLimitGetV2",
    "gpuVoltageOffset": "ctlOverclockGpuMaxVoltageOffsetGetV2",
}


def _read_current(api: IgclApi, handle, field: str) -> float | None:
    """Фактическое текущее значение поля; None — не поддерживается/не читается.

    Нужно для честного «было → стало» в журнале: info.default — это ЗАВОДСКОЕ
    значение, а карта могла быть настроена другим ПО (Intel Graphics Software).
    """
    func = _CURRENT_GETTERS[field]
    if not api.loader.has(func):
        return None
    val = ctypes.c_double(0)
    try:
        _api_call(api, func, handle, ctypes.byref(val))
    except IgclError:
        return None
    return val.value


def current_values(api: IgclApi, handle) -> dict[str, float | None]:
    """Текущие (не дефолтные) значения OC-пунктов через V2 Get-функции.

    Нужно для лога «что было → что стало» перед записью.
    """
    return {field: _read_current(api, handle, field) for field in _CURRENT_GETTERS}


# Небольшие хелперы, чтобы не тянуть ctypes в публичный интерфейс.
def _api_call(api: IgclApi, func_name: str, handle, p_out) -> None:
    """Вызывает V2 Get-функцию (handle, double*) и проверяет результат.

    Raises:
        IgclError: если функция отсутствует в DLL или вернула не-SUCCESS.
    """
    if not api.loader.has(func_name):
        raise IgclError(
            T.CtlResult.ERROR_NOT_SUPPORTED,
            f"{func_name} не поддерживается в текущей версии IGCL",
        )
    fn = getattr(api.loader.dll, func_name)
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
    fn.restype = ctypes.c_uint32
    code = fn(handle, p_out)
    if code != T.CTL_RESULT_SUCCESS:
        raise IgclError(code, func_name)


# --- Кривая V/F: валидация и запись -----------------------------------------
# Жёсткие границы для B580, выведенные из живой кривой (745..995 мВ,
# 770..2800 МГц) с запасом. Это инварианты: кривая вне этих рамок отклоняется
# до вызова IGCL. Драйвер дополнительно валидирует сам (см. коды
# ERROR_CORE_OVERCLOCK_*_OUTSIDE_RANGE), мы не полагаемся только на него.

VF_MIN_VOLTAGE_MV = 600.0  # ниже — нестабильность/провал под нагрузкой
VF_MAX_VOLTAGE_MV = 1250.0  # стоковая кривая B580 доходит до 1195; запас, а не разгон
VF_MIN_FREQ_MHZ = 300.0
VF_MAX_FREQ_MHZ = 3300.0  # сток доходит до 3170; выше — вне диапазона кристалла


@dataclass(frozen=True)
class VfPoint:
    """Точка кривой V/F: напряжение (мВ) и частота (МГц)."""

    voltage_mv: float
    freq_mhz: float


def validate_vf_curve(points: list[VfPoint]) -> None:
    """Проверяет кривую V/F до записи (инварианты + форма).

    Требования:
      - минимум 2 точки (иначе это не кривая);
      - напряжение НЕ убывает по возрастанию частоты (иначе — нестабильный регион);
      - частота НЕ убывает (V/F-кривая монотонна);
      - каждая точка внутри жёстких границ [VF_MIN..VF_MAX].

    Raises:
        SafetyLimitError: любое нарушение формы/границ.
    """
    if len(points) < 2:
        raise SafetyLimitError(f"V/F-кривая: нужно ≥2 точек, получено {len(points)}")

    for i, p in enumerate(points):
        if not (VF_MIN_VOLTAGE_MV <= p.voltage_mv <= VF_MAX_VOLTAGE_MV):
            raise SafetyLimitError(
                f"V/F[{i}]: напряжение {p.voltage_mv} мВ вне жёстких границ "
                f"[{VF_MIN_VOLTAGE_MV}, {VF_MAX_VOLTAGE_MV}]"
            )
        if not (VF_MIN_FREQ_MHZ <= p.freq_mhz <= VF_MAX_FREQ_MHZ):
            raise SafetyLimitError(
                f"V/F[{i}]: частота {p.freq_mhz} МГц вне жёстких границ "
                f"[{VF_MIN_FREQ_MHZ}, {VF_MAX_FREQ_MHZ}]"
            )

    for i in range(1, len(points)):
        prev, cur = points[i - 1], points[i]
        if cur.voltage_mv < prev.voltage_mv:
            raise SafetyLimitError(
                f"V/F[{i}]: напряжение убывает ({prev.voltage_mv} -> {cur.voltage_mv} мВ) — "
                f"кривая должна монотонно возрастать"
            )
        if cur.freq_mhz < prev.freq_mhz:
            raise SafetyLimitError(
                f"V/F[{i}]: частота убывает ({prev.freq_mhz} -> {cur.freq_mhz} МГц) — "
                f"кривая должна монотонно возрастать"
            )


def apply_vf_curve(api: IgclApi, handle, points: list[VfPoint]) -> WriteReport:
    """⚠️ Записывает кастомную кривую V/F после валидации.

    Перед записей дополнительно сверяет количество точек с тем, что вернул
    драйвер (иначе IGCL отклонит), и запускает validate_vf_curve.

    Raises:
        SafetyLimitError: кривая прошла форму, но нарушает границы/монотонность.
        IgclError: ошибка вызова драйвера (в т.ч. несовпадение числа точек).
    """
    validate_vf_curve(points)

    existing = api.read_vf_curve(handle)
    if existing and len(existing) != len(points):
        raise SafetyLimitError(
            f"V/F: драйвер ожидает {len(existing)} точек, передано {len(points)} — "
            f"число точек должно совпадать со стоковой кривой"
        )

    arr = (T.CtlVoltageFrequencyPoint * len(points))()
    for i, p in enumerate(points):
        arr[i].voltage = round(p.voltage_mv)
        arr[i].frequency = round(p.freq_mhz)

    _ensure_waiver(api, handle)  # igcl_api.h: waiver обязателен перед записью кривой
    code = api.loader.dll.ctlOverclockWriteCustomVFCurve(handle, len(points), arr)
    if code != T.CTL_RESULT_SUCCESS:
        raise IgclError(code, "ctlOverclockWriteCustomVFCurve")

    return WriteReport(
        "vfCurve",
        float(len(existing)) if existing else None,
        float(len(points)),
        f"записано {len(points)} точек",
    )
