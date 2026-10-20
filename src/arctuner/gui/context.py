"""GpuContext — жизненный цикл IGCL для GUI + безопасная запись.

Отвечает за:
  - открытие/закрытие IgclApi и поиск дискретной Arc (один раз на сессию);
  - чтение телеметрии/OC/вентиляторов/VF (read-only);
  - запись ТОЛЬКО через tuner.py (двухуровневая проверка) + журнал logbook.

GUI-виджеты не зовут IGCL напрямую — только через этот контекст. Так read-only
и write-пути разделены, а подтверждения/журнал не дублируются в каждой вкладке.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from functools import partial
from typing import Any, ClassVar

from arctuner import tuner
from arctuner.igcl.api import (
    DeviceInfo,
    IgclApi,
    IgclError,
    OcControlInfo,
    TelemetrySnapshot,
)
from arctuner.igcl.types import CtlFanProperties
from arctuner.logbook import Logbook, default_logbook_path
from arctuner.telemetry import PowerMeter, find_arc_adapter
from arctuner.tuner import SafetyLimitError, VfPoint, WriteReport


@dataclass(frozen=True)
class FanInfo:
    """Состояние одного вентилятора (read-only снимок)."""

    index: int
    rpm: int
    props: CtlFanProperties


class GpuContext:
    """Открытая IGCL-сессия на одно устройство (дискретная Arc)."""

    def __init__(self) -> None:
        self._api: IgclApi | None = None
        self._handle: object | None = None
        self._info: DeviceInfo | None = None
        self._log = Logbook(default_logbook_path())
        # Расчёт ватт из счётчиков энергии: одна база на всю сессию опроса.
        self._power_meter = PowerMeter()

    # --- жизненный цикл ---

    @property
    def is_open(self) -> bool:
        return self._api is not None

    @property
    def info(self) -> DeviceInfo | None:
        return self._info

    @property
    def log(self) -> Logbook:
        return self._log

    def open(self) -> DeviceInfo:
        """ctlInit + поиск Arc. Повторный вызов — no-op.

        Raises:
            IgclError: если драйвер недоступен/нет устройств.
            SafetyLimitError: если дискретная Arc не найдена.
        """
        if self._api is not None and self._info is not None:
            return self._info
        api = IgclApi()
        api.initialize()
        try:
            found = find_arc_adapter(api)
        except Exception:
            api.shutdown()
            raise
        if not found:
            api.shutdown()
            raise SafetyLimitError("дискретная Intel Arc не найдена")
        handle, info = found
        self._api, self._handle, self._info = api, handle, info
        return info

    def close(self) -> None:
        """ctlClose (безопасно вызывать повторно)."""
        if self._api is not None:
            with contextlib.suppress(IgclError):
                self._api.shutdown()
        self._api = None
        self._handle = None
        self._info = None

    def _require(self) -> tuple[IgclApi, object]:
        if self._api is None or self._handle is None:
            raise SafetyLimitError("IGCL-сессия не открыта")
        return self._api, self._handle

    # --- чтение (read-only) ---

    def telemetry(self) -> TelemetrySnapshot:
        api, handle = self._require()
        # enrich добавляет среднюю мощность (Вт) из счётчиков энергии.
        return self._power_meter.enrich(api.read_power_telemetry(handle))

    def oc_ranges(self) -> dict[str, OcControlInfo | bool]:
        api, handle = self._require()
        return api.read_oc_properties(handle)

    def field_grid(self, field: str) -> tuple[float, float] | None:
        """Сетка IGCL поля (min, step) для авторазгона; None — не поддерживается.

        IGCL отклоняет значения, не кратные шагу от min (36.6 при step=1.0) —
        движок приводит кандидаты к этой сетке до записи.
        """
        info = self.oc_ranges().get(field)
        if isinstance(info, bool) or info is None or not info.supported or info.step <= 0:
            return None
        return (info.min, info.step)

    def current_values(self) -> dict[str, float | None]:
        api, handle = self._require()
        return tuner.current_values(api, handle)

    def fans(self) -> list[FanInfo]:
        api, handle = self._require()
        out: list[FanInfo] = []
        for i, fan in enumerate(api.enum_fans(handle)):
            props = api.get_fan_properties(fan)
            rpm = api.get_fan_speed(fan)
            out.append(FanInfo(index=i, rpm=rpm, props=props))
        return out

    def vf_curve(self) -> list[tuple[int, int]]:
        """Кривая V/F как пары (мВ, МГц)."""
        api, handle = self._require()
        return [(p.voltage, p.frequency) for p in api.read_vf_curve(handle)]

    # --- запись (только через tuner + журнал) ---

    def _write(self, fn, value: float, *extra, **kwargs) -> WriteReport:
        api, handle = self._require()
        report = fn(api, handle, value, *extra, **kwargs)
        self._log.record(
            report.field,
            report.old,
            report.new,
            device=self._info.name if self._info else "",
            note=report.note,
        )
        return report

    def set_power_limit(self, percent: float) -> WriteReport:
        return self._write(tuner.set_power_limit, percent)

    def set_freq_offset(self, mhz: float) -> WriteReport:
        return self._write(tuner.set_gpu_freq_offset, mhz)

    def set_voltage_offset(self, percent: float) -> WriteReport:
        return self._write(tuner.set_gpu_voltage_offset, percent)

    def set_temp_limit(self, celsius: float) -> WriteReport:
        return self._write(tuner.set_temperature_limit, celsius)

    def set_vram_speed(self, gbps: float) -> WriteReport:
        """>20 Гбит/с (сверх номинала SC20) — пользователь уже подтвердил в диалоге."""
        return self._write(
            tuner.set_vram_speed,
            gbps,
            allow_beyond_nominal=gbps > tuner.MAX_VRAM_SPEED_GBPS,
        )

    def reset_oc(self) -> WriteReport:
        api, handle = self._require()
        report = tuner.reset_to_default(api, handle)
        self._log.record(
            report.field,
            report.old,
            report.new,
            device=self._info.name if self._info else "",
            note=report.note,
        )
        return report

    # --- вентиляторы ---

    def set_fan_speed(self, fan_index: int, rpm: int) -> None:
        api, handle = self._require()
        fans = api.enum_fans(handle)
        if not 0 <= fan_index < len(fans):
            raise SafetyLimitError(f"вентилятора #{fan_index} нет")
        props = api.get_fan_properties(fans[fan_index])
        if not props.canControl:
            raise SafetyLimitError(f"вентилятор #{fan_index} не управляется ПО")
        if props.maxRPM > 0 and rpm > props.maxRPM:
            raise SafetyLimitError(f"{rpm} RPM > maxRPM {props.maxRPM}")
        api.set_fan_fixed_speed(fans[fan_index], rpm)
        self._log.record(
            f"fan[{fan_index}]",
            None,
            float(rpm),
            device=self._info.name if self._info else "",
            note="фиксация скорости",
        )

    def reset_fans(self) -> None:
        api, handle = self._require()
        for fan in api.enum_fans(handle):
            api.set_fan_default(fan)
        self._log.record(
            "fans",
            None,
            None,
            device=self._info.name if self._info else "",
            note="возврат в авто",
        )

    # --- кривая V/F ---

    def apply_vf_curve(self, points: list[VfPoint]) -> WriteReport:
        api, handle = self._require()
        report = tuner.apply_vf_curve(api, handle, points)
        self._log.record(
            report.field,
            report.old,
            report.new,
            device=self._info.name if self._info else "",
            note=report.note,
        )
        return report

    # --- откат (та же карта полей, что в CLI `log --rollback`) ---

    #: поле журнала → функция tuner, восстанавливающая значение.
    #: Для памяти allow_beyond_nominal=True: значение УЖЕ было на карте (его
    #: записал ArcTune), значит явное согласие уже было дано. Без этого откат
    #: журнала с old > 20 Гбит/с падал SafetyLimitError.
    _ROLLBACK_MAP: ClassVar[dict[str, Any]] = {
        "powerLimit": tuner.set_power_limit,
        "gpuFrequencyOffset": tuner.set_gpu_freq_offset,
        "gpuVoltageOffset": tuner.set_gpu_voltage_offset,
        "temperatureLimit": tuner.set_temperature_limit,
        "vramMemSpeedLimit": partial(tuner.set_vram_speed, allow_beyond_nominal=True),
    }

    def rollback_targets(self) -> dict[str, float | None]:
        """Исходные значения из журнала (что восстанавливать)."""
        return self._log.rollback_targets()

    def rollback(self) -> list[tuple[str, float]]:
        """Возвращает настройки к исходным значениям журнала.

        Применяет по одной (CLI-совместимо), помечая каждое восстановленное
        поле как restored. Остановка — на первой ошибке: виновник остаётся
        без изменений, остальные откатываются при следующем запуске.

        Returns:
            Список (поле, восстановленное значение), применённых до остановки.

        Raises:
            SafetyLimitError: сессия не открыта.
            IgclError / SafetyLimitError: ошибка вызова или выход за диапазон.
        """
        api, handle = self._require()
        done: list[tuple[str, float]] = []
        for name, value in self._log.rollback_targets().items():
            fn = self._ROLLBACK_MAP.get(name)
            if fn is None or value is None:
                continue
            fn(api, handle, value)  # type: ignore[operator]
            self._log.mark_restored(name)
            done.append((name, value))
        return done

    def __enter__(self) -> GpuContext:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


#: Имена полей, поддерживаемых откатом (для предпросмотра в GUI «Журнал»):
#: семантика logbook.rollback_targets — «самое старое known old на поле».
#: Вентиляторы откатываются кнопкой «Вернуть в авто», не через журнал.
ROLLBACKABLE_FIELDS: frozenset[str] = frozenset(GpuContext._ROLLBACK_MAP)
