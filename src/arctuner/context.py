"""GpuContext — жизненный цикл IGCL + безопасная запись (без зависимости от Qt).

Отвечает за:
  - открытие/закрытие IgclApi и поиск дискретной Arc (один раз на сессию);
  - чтение телеметрии/OC/вентиляторов/VF (read-only);
  - запись ТОЛЬКО через tuner.py (двухуровневая проверка) + журнал logbook.

Контекст общий для GUI и CLI (autofind): оба идут одним путём «tuner + журнал»,
поэтому поведение отката и текст ошибок совпадают. GUI-виджеты не зовут IGCL
напрямую — только через этот контекст.
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
from arctuner.logbook import (
    RESET_FANS_FIELD,
    RESET_OC_FIELD,
    JournalWriteError,
    Logbook,
    default_logbook_path,
)
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

    def _record(
        self,
        field_name: str,
        old: float | None,
        new: float | None,
        note: str = "",
    ) -> None:
        """Пишет изменение в журнал с именем устройства текущей сессии."""
        self._log.record(
            field_name,
            old,
            new,
            device=self._info.name if self._info else "",
            note=note,
        )

    def _restore_old(
        self,
        api: IgclApi,
        handle: object,
        fn,
        report: WriteReport,
        *extra,
        **kwargs,
    ) -> tuple[bool, str]:
        """Пытается вернуть старое значение тем же tuner-вызовом.

        Returns:
            (успех, пояснение для текста ошибки). Исключения компенсации не
            глотаются молча: их текст попадает в JournalWriteError.
        """
        if report.old is None:
            return False, "старое значение не прочитано"
        restore_kwargs = dict(kwargs)
        if report.field == "vramMemSpeedLimit":
            # Повторное согласие не нужно: значение УЖЕ было на карте.
            restore_kwargs["allow_beyond_nominal"] = report.old > tuner.MAX_VRAM_SPEED_GBPS
        try:
            fn(api, handle, report.old, *extra, **restore_kwargs)
        except (IgclError, SafetyLimitError, OSError) as exc:
            return False, f"возврат не удался ({type(exc).__name__}: {exc})"
        return True, "карта возвращена к старому значению"

    def _write(self, fn, value: float, *extra, **kwargs) -> WriteReport:
        api, handle = self._require()
        report = fn(api, handle, value, *extra, **kwargs)
        try:
            self._record(report.field, report.old, report.new, note=report.note)
        except OSError as exc:
            restored, detail = self._restore_old(api, handle, fn, report, *extra, **kwargs)
            if restored:
                detail += " — настройка отменена"
            else:
                detail += " — состояние карты неизвестно"
            raise JournalWriteError(f"{report.field}: журнал не записан ({exc}); {detail}") from exc
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
        """Сброс OC к дефолту + маркер журнала, инвалидирующий прежние цели."""
        api, handle = self._require()
        report = tuner.reset_to_default(api, handle)
        try:
            self._record(
                RESET_OC_FIELD,
                None,
                None,
                note=(
                    "сброс OC к дефолту; прежние цели отката недействительны (история сохранена)"
                ),
            )
        except OSError as exc:
            # Карта уже в дефолте — компенсировать нечего, но молчать нельзя.
            raise JournalWriteError(
                "OC сброшены к дефолту, но журнал не записан "
                f"({exc}); состояние карты известно (дефолт), "
                "прежние цели отката могли остаться"
            ) from exc
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
        if props.maxRPM <= 0:
            raise SafetyLimitError(
                f"вентилятор #{fan_index}: maxRPM неизвестен — запись заблокирована"
            )
        if rpm > props.maxRPM:
            raise SafetyLimitError(f"{rpm} RPM > maxRPM {props.maxRPM}")
        old_rpm = api.get_fan_speed(fans[fan_index])
        api.set_fan_fixed_speed(fans[fan_index], rpm)
        try:
            self._record(
                f"fan[{fan_index}]",
                float(old_rpm),
                float(rpm),
                note="фиксация скорости",
            )
        except OSError as exc:
            try:
                api.set_fan_default(fans[fan_index])
                detail = "вентилятор возвращён в авто — настройка отменена"
            except (IgclError, OSError) as rb_exc:
                detail = f"вернуть авто не удалось — состояние неизвестно ({rb_exc})"
            raise JournalWriteError(
                f"fan[{fan_index}]: журнал не записан ({exc}); {detail}"
            ) from exc

    def reset_fans(self) -> None:
        api, handle = self._require()
        for fan in api.enum_fans(handle):
            api.set_fan_default(fan)
        try:
            self._record(
                RESET_FANS_FIELD,
                None,
                None,
                note="вентиляторы возвращены в авто (история fan[*] сохранена)",
            )
        except OSError as exc:
            # Карта уже в авто: честно сообщаем, что журнал не зафиксировал сброс.
            raise JournalWriteError(
                "вентиляторы возвращены в авто, но журнал не записан "
                f"({exc}); состояние вентиляторов известно (авто), "
                "история сброса не зафиксирована"
            ) from exc

    # --- кривая V/F ---

    def apply_vf_curve(self, points: list[VfPoint]) -> WriteReport:
        api, handle = self._require()
        # Старую кривую читаем ДО записи: при сбое журнала её нужно вернуть.
        old_curve = api.read_vf_curve(handle)
        report = tuner.apply_vf_curve(api, handle, points)
        try:
            self._record(report.field, report.old, report.new, note=report.note)
        except OSError as exc:
            detail = "старая кривая не прочитана — состояние V/F неизвестно"
            if old_curve:
                try:
                    old_points = [VfPoint(p.voltage, p.frequency) for p in old_curve]
                    tuner.apply_vf_curve(api, handle, old_points)
                    detail = "старая кривая V/F восстановлена — настройка отменена"
                except (IgclError, SafetyLimitError, OSError) as rb_exc:
                    detail = (
                        "вернуть старую кривую не удалось — состояние V/F неизвестно "
                        f"({type(rb_exc).__name__}: {rb_exc})"
                    )
            raise JournalWriteError(f"{report.field}: журнал не записан ({exc}); {detail}") from exc
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
#: семантика logbook.rollback_targets — «самое старое known old на поле после
#: последнего сброса OC». Вентиляторы откатываются кнопкой «Вернуть в авто».
ROLLBACKABLE_FIELDS: frozenset[str] = frozenset(GpuContext._ROLLBACK_MAP)

__all__ = ["ROLLBACKABLE_FIELDS", "FanInfo", "GpuContext"]
