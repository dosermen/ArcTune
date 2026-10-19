"""Движок адаптивного авторазгона.

Стратегия: адаптивный спуск к границе стабильности:
  1. От текущего значения (best) делаем шаг в направлении режима
     (frequency — вверх, МГц; undervolt — вниз, %).
  2. Шаг верифицируется: нагрузка (activity_counter растёт), гарды
     (температура), опциональный watchdog (TDR, заморозка телеметрии).
  3. Стабильный шаг принимается как best, следующий шаг — меньше
     (×0.8, не ниже min_step): к границе подходим аккуратнее.
  4. Провал шага → откат к последнему СТАБИЛЬНОМУ значению (не к
     провалившемуся) и стоп. Гарантия как в autofind.py.

Все записи идут через GpuContext (tuner-проверки + журнал logbook) —
движок никогда не вызывает tuner.py напрямую.

Внимание (B580): undervolt через gpuVoltageOffset ограничен драйвером
диапазоном 0…+ (min=0), реальный undervolt — только кривая V/F. Движок
не обходится это ограничение; при попытке шага ниже min поиск честно
остановится с пояснением.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from arctuner.autofind import MODE_FREQUENCY, MODE_UNDERVOLT
from arctuner.autotune import loader
from arctuner.igcl.api import IgclError, TelemetrySnapshot
from arctuner.tuner import SafetyLimitError


class TuningContext(Protocol):
    """Минимальный интерфейс GpuContext для движка (без импорта GUI-слоя).

    Все методы — с проверками tuner.py и записью в журнал logbook.
    """

    def current_values(self) -> dict[str, float | None]:
        """Текущие значения OC-полей."""
        ...

    def set_freq_offset(self, mhz: float) -> Any:
        """Запись смещения частоты ядра, МГц."""
        ...

    def set_voltage_offset(self, percent: float) -> Any:
        """Запись смещения напряжения ядра, % (B580: только 0…+)."""
        ...

    def telemetry(self) -> TelemetrySnapshot:
        """Снимок телеметрии."""
        ...

    def reset_oc(self) -> Any:
        """Полный сброс OC к дефолту (крайняя мера отката)."""
        ...


@dataclass
class TuningConfig:
    """Настройки поиска авторазгона.

    step — МГц для MODE_FREQUENCY, % для MODE_UNDERVOLT.
    """

    mode: str = (
        MODE_FREQUENCY  # по умолчанию частота: undervolt через offset на B580 ограничен (0…+)
    )
    step: float = 15.0  # стартовый шаг (МГц / %)
    min_step: float = 3.0  # ниже этого шага граница считается найденной
    max_steps: int = 30  # потолок итераций
    settle_seconds: float = 1.0  # пауза после шага перед проверками
    temp_ceiling_c: float = 80.0  # откат при превышении
    require_load: bool = True  # шаг засчитывается только под нагрузкой
    load_timeout: float = 6.0  # ожидание появления нагрузки, с
    external_cmd: str | None = None  # команда стресс-нагрузчика (запускается один раз)


@dataclass
class TuningResult:
    """Итог авторазгона."""

    mode: str
    baseline: float
    best_stable: float
    steps_taken: int
    stopped_reason: str
    history: list[dict] = field(default_factory=list)
    fitness_history: list[dict] = field(default_factory=list)

    @property
    def improvement(self) -> float:
        """Смещение от базы по оси режима (знак зависит от режима)."""
        return self.best_stable - self.baseline

    def best_profile(self) -> dict:
        """Последний пройденный шаг (или базовое значение, если шагов не было)."""
        best = None
        for entry in self.history:
            if entry.get("passed", False):
                best = entry
        if best is None:
            return {"mode": self.mode, "value": self.baseline, "verified": False}
        return best


class TuningEngine:
    """Адаптивный спуск с верификацией каждого шага (запись через TuningContext)."""

    #: во сколько раз уменьшается шаг после стабильного шага (посадка к границе)
    STEP_DECAY: float = 0.8

    def __init__(
        self,
        ctx: TuningContext,
        cfg: TuningConfig | None = None,
        *,
        watchdog: Any | None = None,
    ) -> None:
        """Создаёт движок.

        Args:
            ctx: контекст сессии (GpuContext либо совместимый объект).
            cfg: параметры поиска.
            watchdog: опциональный Watchdog; его check() вызывается на каждом
                шаге, первое событие — откат к best и стоп.
        """
        self._ctx = ctx
        self._cfg = cfg or TuningConfig()
        self._watchdog = watchdog

    def _apply(self, value: float) -> None:
        """Применяет значение через контекст (двухуровневая проверка + журнал)."""
        if self._cfg.mode == MODE_FREQUENCY:
            self._ctx.set_freq_offset(value)
        elif self._cfg.mode == MODE_UNDERVOLT:
            self._ctx.set_voltage_offset(value)
        else:
            raise SafetyLimitError(f"неизвестный режим автопоиска: {self._cfg.mode}")

    def _restore(self, value: float) -> None:
        """Гарантированный откат к значению.

        Последняя попытка при невозможности шага — полный сброс к дефолту,
        чтобы не оставить полузадранное состояние (как в autofind.py).
        """
        try:
            self._apply(value)
        except (IgclError, SafetyLimitError):
            self._ctx.reset_oc()

    def run(
        self,
        *,
        on_step: Callable[[int, float, TelemetrySnapshot], None] | None = None,
        stop_check: Callable[[], bool] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> TuningResult:
        """Запускает адаптивный спуск. ⚠️ Меняет настройки карты.

        Гарантия отката: при любом исходе (успех/гард/stopt/исключение) карта
        остаётся на последнем СТАБИЛЬНОМ значении, а не на провалившемся —
        restore выполняется только когда карта реально не на best.

        Args:
            on_step: колбэк (шаг, значение, снимок) для прогресса — вызывается
                после СТАБИЛЬНОГО шага.
            stop_check: опциональный предикат отмены (проверяется перед шагом);
                True → честный откат к best и завершение.
            on_status: колбэк (текст) для «тихих» этапов (ожидание нагрузки,
                проверка телеметрии) — иначе они выглядят как зависание UI.

        Returns:
            TuningResult с историей и лучшим стабильным значением.

        Raises:
            SafetyLimitError: сессия не открыта / исходное значение не читается.
            IgclError: ошибка драйвера (после отката к best).
            OSError: сбой запуска/остановки внешнего стресс-процесса.
        """
        cfg = self._cfg
        ctx = self._ctx
        field_name = "gpuVoltageOffset" if cfg.mode == MODE_UNDERVOLT else "gpuFrequencyOffset"

        baseline = ctx.current_values().get(field_name)
        if baseline is None:
            raise SafetyLimitError(
                f"не удалось прочитать исходное {field_name} — автопоиск отменён"
            )

        # Направление: frequency — вверх; undervolt — вниз (ограничение B580 см. в шапке).
        direction = -1.0 if cfg.mode == MODE_UNDERVOLT else 1.0

        history: list[dict] = []
        fitness_history: list[dict] = []
        best = baseline  # последний стабильный; точка отката при любом провале
        current_step = float(cfg.step)
        steps_taken = 0
        reason = "достигнут лимит шагов"
        need_restore = False  # карта НЕ на best → нужен откат
        stress: Any | None = None  # внешний процесс нагрузки

        if on_status:
            on_status(f"старт: база {baseline:g}, шаг {cfg.step:g} {direction:+.0f}")

        try:
            if cfg.external_cmd:
                stress = loader.start_external(cfg.external_cmd)
                if stress is None:
                    return TuningResult(
                        mode=cfg.mode,
                        baseline=baseline,
                        best_stable=baseline,
                        steps_taken=0,
                        stopped_reason=f"не удалось запустить стресс-нагрузчик: {cfg.external_cmd}",
                        history=history,
                        fitness_history=fitness_history,
                    )

            for i in range(1, cfg.max_steps + 1):
                if stop_check is not None and stop_check():
                    reason = "остановлено пользователем — откат к стабильному"
                    need_restore = True
                    break

                # candidate считается ОТ BEST: стабильные шаги накапливаются,
                # а не нанизываются мелкими от baseline.
                candidate = round(best + direction * current_step, 6)

                try:
                    self._apply(candidate)
                except SafetyLimitError as e:
                    reason = f"достигнут диапазон IGCL на шаге {i}: {e}"
                    if cfg.mode == MODE_UNDERVOLT:
                        reason += (
                            " — undervolt через смещение на B580 ограничен (min=0);"
                            " используйте кривую V/F"
                        )
                    need_restore = True
                    break

                if cfg.settle_seconds > 0:
                    time.sleep(cfg.settle_seconds)

                if cfg.require_load:
                    if on_status:
                        on_status(f"шаг {i}: кандидат {candidate:+g} — ожидание нагрузки…")
                    if not loader.verify_stress(
                        ctx.telemetry, interval=0.5, timeout=cfg.load_timeout
                    ):
                        reason = f"нет нагрузки на шаге {i} — стабильность не подтверждена"
                        need_restore = True
                        break
                elif on_status:
                    on_status(f"шаг {i}: кандидат {candidate:+g} — замер телеметрии…")

                snap = ctx.telemetry()

                if self._watchdog is not None:
                    evt = self._watchdog.check(snap)
                    if evt is not None:
                        reason = f"watchdog: {evt.type} — {evt.detail}"
                        need_restore = True
                        break

                if snap.gpu_temp_c is not None and snap.gpu_temp_c > cfg.temp_ceiling_c:
                    reason = (
                        f"температура {snap.gpu_temp_c:.0f}°C > потолка {cfg.temp_ceiling_c:.0f}°C"
                    )
                    need_restore = True
                    break

                fitness = self._evaluate_fitness(snap)

                history.append(
                    {
                        "step": i,
                        "value": candidate,
                        "temp_c": snap.gpu_temp_c,
                        "power_percent": snap.gpu_power_percent,
                        "passed": True,
                        "reason": None,
                        "fitness": fitness,
                    }
                )
                fitness_history.append({"step": i, "fitness": fitness, "value": candidate})

                if on_step:
                    on_step(i, candidate, snap)

                best = candidate
                steps_taken = i
                current_step = round(current_step * self.STEP_DECAY, 6)
                if current_step < cfg.min_step:
                    reason = "шаг ниже минимального — граница найдена"
                    break

        except KeyboardInterrupt:
            reason = "прервано пользователем (Ctrl+C) — выполнен откат"
            need_restore = True
            from arctuner.autofind import AutofindAbortError

            raise AutofindAbortError(reason) from None
        finally:
            if need_restore:
                self._restore(best)
            if stress is not None:
                loader.terminate_stress(stress)

        return TuningResult(
            mode=cfg.mode,
            baseline=baseline,
            best_stable=best,
            steps_taken=steps_taken,
            stopped_reason=reason,
            history=history,
            fitness_history=fitness_history,
        )

    def _evaluate_fitness(self, snap: TelemetrySnapshot) -> float | None:
        """Оценка состояния: меньше мощность/температура — лучше.

        Returns:
           fitness или None, если датчики недоступны (честно не придумываем чисел).
        """
        power = snap.gpu_power_percent
        temp = snap.gpu_temp_c
        if power is None or temp is None:
            return None
        power_factor = (1.0 - power / 100.0) * 5.0
        temp_factor = (1.0 - temp / 100.0) * 3.0
        return round(power_factor + temp_factor, 4)


__all__ = ["TuningConfig", "TuningContext", "TuningEngine", "TuningResult"]
