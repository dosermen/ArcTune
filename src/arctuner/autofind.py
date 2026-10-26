"""Модуль совместимости: старый автопоиск (⚠️ меняет настройки карты).

Рабочая логика ПЕРЕЕХАЛА в :mod:`arctuner.autotune.engine` (TuningEngine):
CLI `autofind` и GUI «Авторазгон» используют её. Этот модуль оставлен для
прямых скриптов и старых импортов: публичные имена `MODE_UNDERVOLT`,
`MODE_FREQUENCY`, `AutofindAbortError`, `AutofindConfig`, `AutofindResult`
и функция `autofind()` здесь сохранены; константы и исключение
переэкспортируются из engine.

Как это работает (см. docs/roadmap.md, docs/overclocking.md):
  1. фиксируем базовое состояние;
  2. итеративно двигаем ОДИН параметр малым шагом (по умолчанию — undervolt:
     снижение gpuVoltageOffset, безопасное направление: меньше нагрев/мощность);
  3. после каждого шага — пауза на стабилизацию + чтение телеметрии;
  4. Гарды: температура ниже потолка, GPU реально под нагрузкой
     (дельта activity_counter > 0), отсутствие «аварийных» причин ограничения;
  5. при срабатывании гарда — ОТКАТ на последнее стабильное значение и стоп.

Честное ограничение. Телеметрия НЕ видит compute-ошибки и крэши (TDR/driver
reset) — их заметит только внешняя нагрузка с известным исходом. Поэтому:
  - перед autofind запустите стресс/бенчмарк в другом окне (нужна нагрузка);
  - при наличии внешнего критерия стабильности передайте stability_check —
    он вернёт False, если задача упала/выдала ошибку.
Без нагрузки шаг засчитывается как «тихо», а не «стабильно» — это предупреждается.

Инварианты проекта соблюдаются: никаких страпов/vBIOS/лимита >120%. Используются
только set-функции, прошедшие двухуровневую проверку tuner.py.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from arctuner.autotune.engine import (
    MODE_FREQUENCY,
    MODE_UNDERVOLT,
    AutofindAbortError,
)
from arctuner.igcl.api import IgclApi, IgclError, TelemetrySnapshot
from arctuner.tuner import SafetyLimitError, current_values, set_gpu_voltage_offset


@dataclass
class AutofindConfig:
    """Параметры автопоиска."""

    mode: str = MODE_UNDERVOLT  # что двигаем
    step: float = 1.0  # шаг за итерацию (%, для undervolt) или МГц
    max_steps: int = 20  # потолок итераций
    settle_seconds: float = 1.0  # пауза после шага перед чтением
    temp_ceiling_c: float = 80.0  # откат, если горячее
    require_load: bool = True  # считать шаг только при реальной нагрузке
    load_timeout_seconds: float = 3.0  # ждать появления нагрузки перед оценкой

    def validate(self) -> None:
        """Проверяет параметры ДО первой записи на карту.

        Без этого отрицательный step разворачивал поиск в опасную сторону
        (undervolt «вверх» по напряжению), а отрицательный settle ронял
        ValueError уже ПОСЛЕ записи — без отката.

        Raises:
            SafetyLimitError: некорректный режим/шаг/пауза/лимит шагов.
        """
        if self.mode not in (MODE_UNDERVOLT, MODE_FREQUENCY):
            raise SafetyLimitError(f"неизвестный режим autofind: {self.mode!r}")
        for name, value in (
            ("step", self.step),
            ("settle_seconds", self.settle_seconds),
            ("temp_ceiling_c", self.temp_ceiling_c),
            ("load_timeout_seconds", self.load_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SafetyLimitError(f"{name} — число ожидается, получено: {value!r}")
            if not math.isfinite(value):
                raise SafetyLimitError(f"{name} — бесконечность/NaN недопустимы")
        if self.step <= 0:
            raise SafetyLimitError(f"step должен быть > 0: {self.step}")
        if self.settle_seconds < 0 or self.temp_ceiling_c <= 0:
            raise SafetyLimitError("settle_seconds >= 0, temp_ceiling_c > 0")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int):
            raise SafetyLimitError(f"max_steps — целое ожидается, получено: {self.max_steps!r}")
        if self.max_steps <= 0:
            raise SafetyLimitError(f"max_steps должен быть > 0: {self.max_steps}")


@dataclass
class AutofindResult:
    """Итог автопоиска."""

    mode: str
    baseline: float
    best_stable: float
    steps_taken: int
    stopped_reason: str
    history: list[dict] = field(default_factory=list)

    @property
    def improvement(self) -> float:
        """Насколько ушли от базы (знак зависит от режима)."""
        return self.best_stable - self.baseline


def _activity(api: IgclApi, handle, snap: TelemetrySnapshot | None = None) -> int | None:
    t = snap if snap is not None else api.read_power_telemetry(handle)
    return t.activity_counter


def _wait_for_load(api: IgclApi, handle, timeout: float) -> bool:
    """Ждёт появления нагрузки GPU (дельта activity_counter > 0). Возвращает True, если есть."""
    if timeout <= 0:
        t1 = api.read_power_telemetry(handle)
        # Небольшая пауза между двумя чтениями — activity_counter может обновляться
        # не мгновенно (зависит от драйвера). Без задержки t1 == t2 при idle GPU.
        time.sleep(0.1)
        t2 = api.read_power_telemetry(handle)
        return bool(
            t1.activity_counter is not None
            and t2.activity_counter is not None
            and t2.activity_counter > t1.activity_counter
        )
    deadline = time.monotonic() + timeout
    prev = _activity(api, handle)
    while time.monotonic() < deadline:
        time.sleep(0.2)
        cur = _activity(api, handle)
        if prev is not None and cur is not None and cur > prev:
            return True
        prev = cur
    return False


def _guard_failed(t: TelemetrySnapshot, cfg: AutofindConfig, under_load: bool) -> str | None:
    """Возвращает причину отката или None, если шаг стабилен."""
    if t.gpu_temp_c is not None and t.gpu_temp_c > cfg.temp_ceiling_c:
        return f"температура {t.gpu_temp_c:.0f}°C > потолка {cfg.temp_ceiling_c:.0f}°C"
    if cfg.require_load and not under_load:
        return "нет нагрузки GPU — стабильность не подтверждена"
    # Причины ограничения не сами по себе «нестабильность», но фиксируем их.
    return None


def _set_value(api: IgclApi, handle, cfg: AutofindConfig, value: float) -> None:
    """Применяет итерационное значение через tuner (двухуровневая проверка)."""
    # MODE_FREQUENCY (настройка частоты) не вызывается из CLI/GUI —
    # только из скриптов напрямую; импорт здесь намеренно ленивый.
    if cfg.mode == MODE_FREQUENCY:
        from arctuner.tuner import set_gpu_freq_offset as _freq

        _freq(api, handle, value)
    elif cfg.mode == MODE_UNDERVOLT:
        set_gpu_voltage_offset(api, handle, value)
    else:
        raise SafetyLimitError(f"неизвестный режим autofind: {cfg.mode}")


def autofind(
    api: IgclApi,
    handle,
    cfg: AutofindConfig | None = None,
    *,
    stability_check: Callable[[], bool] | None = None,
    on_step: Callable[[int, float, TelemetrySnapshot], None] | None = None,
) -> AutofindResult:
    """Ищет границу стабильности, двигая один параметр. ⚠️ Меняет настройки карты.

    Гарантия отката: при любом исходе (успех/гард/исключение/прерывание) карта
    остаётся на последнем СТАБИЛЬНОМ значении, а не на провалившемся.

    Args:
        api: инициализированный IgclApi.
        handle: handle дискретной Arc.
        cfg: параметры поиска (по умолчанию undervolt, шаг 1%, ≤20 шагов).
        stability_check: опциональный внешний критерий (стресс вернул OK).
            False → откат. Если None — опираемся только на телеметрию+нагрузку.
        on_step: колбэк (шаг, значение, снимок) для прогресса/лога.

    Returns:
        AutofindResult с историей и найденной точкой.

    Raises:
        SafetyLimitError: запрос вышел за диапазон IGCL.
        IgclError: ошибка драйвера.
        AutofindAbortError: пользователь прервал (Ctrl+C) — карта откатена.
    """
    cfg = cfg or AutofindConfig()
    cfg.validate()  # до любых обращений к карте: битый конфиг не трогает карту

    baseline_vals = current_values(api, handle)
    field_name = "gpuVoltageOffset" if cfg.mode == MODE_UNDERVOLT else "gpuFrequencyOffset"
    baseline = baseline_vals.get(field_name)
    if baseline is None:
        raise SafetyLimitError(f"не удалось прочитать исходное {field_name} — автопоиск отменён")

    # Направление: undervolt — вниз (уменьшаем %), frequency — вверх (больше МГц).
    direction = -1.0 if cfg.mode == MODE_UNDERVOLT else 1.0

    best = baseline
    steps_taken = 0
    history: list[dict] = []
    stopped_reason = "достигнут лимит шагов"

    def restore(value: float) -> None:
        """Возвращает карту к значению (гарантированный откат)."""
        try:
            _set_value(api, handle, cfg, value)
        except (IgclError, SafetyLimitError):
            # Последняя попытка — полный сброс к дефолту, чтобы не оставить
            # полузадранное состояние.
            from arctuner.tuner import reset_to_default

            reset_to_default(api, handle)

    try:
        for i in range(1, cfg.max_steps + 1):
            candidate = round(baseline + direction * cfg.step * i, 6)

            try:
                _set_value(api, handle, cfg, candidate)
            except SafetyLimitError as e:
                stopped_reason = f"достигнут диапазон IGCL на шаге {i}: {e}"
                break

            time.sleep(cfg.settle_seconds)

            under_load = _wait_for_load(api, handle, cfg.load_timeout_seconds)
            snap = api.read_power_telemetry(handle)

            if on_step:
                on_step(i, candidate, snap)

            reason = _guard_failed(snap, cfg, under_load)
            if reason is None and stability_check is not None and not stability_check():
                reason = "внешняя проверка стабильности провалена"

            entry = {
                "step": i,
                "value": candidate,
                "temp_c": snap.gpu_temp_c,
                "power_percent": snap.gpu_power_percent,
                "under_load": under_load,
                "passed": reason is None,
                "reason": reason,
            }
            history.append(entry)

            if reason is not None:
                stopped_reason = f"откат на шаге {i}: {reason}"
                restore(best)
                return AutofindResult(
                    mode=cfg.mode,
                    baseline=baseline,
                    best_stable=best,
                    steps_taken=steps_taken,
                    stopped_reason=stopped_reason,
                    history=history,
                )

            # Шаг стабилен — принимаем.
            best = candidate
            steps_taken = i

    except KeyboardInterrupt:
        stopped_reason = "прервано пользователем (Ctrl+C) — выполнен откат"
        restore(best)
        raise AutofindAbortError(stopped_reason) from None
    except (IgclError, SafetyLimitError):
        # Любая ошибка драйвера — не оставляем последний кандидат.
        stopped_reason = "ошибка драйвера — выполнен откат"
        restore(best)
        raise
    except Exception:
        # Сбой пользовательского колбэка (on_step/stability_check) тоже не
        # имеет права оставить карту на необработанном кандидате.
        stopped_reason = "неожиданная ошибка — выполнен откат"
        restore(best)
        raise
    else:
        # Нормальный выход (лимит шагов) — карта уже на best (последнем стабильном).
        # Если ни один шаг не прошёл — best == baseline, ничего не меняем.
        pass

    return AutofindResult(
        mode=cfg.mode,
        baseline=baseline,
        best_stable=best,
        steps_taken=steps_taken,
        stopped_reason=stopped_reason,
        history=history,
    )
