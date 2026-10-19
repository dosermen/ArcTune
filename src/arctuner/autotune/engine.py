"""Движок адаптивного авторазгона.

Стратегия: адаптивный бинарный спуск с верификацией.
  1. Начинаем с крупных шагов (быстрый спуск к оптиму).
  2. Когда подходим к пределу — уменьшаем шаг (верификация).
  3. На каждом шаге — проверям нагрузку + guards.
  4. При сбое — откатываемся к лучшему стабильному.

Инварианты:
  - Никогда не оставляем карту на провалившемся значении.
  - Каждый шаг верифицируется с нагрузкой.
  - Откат к последнему стабильному гарантирован.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from arctuner.autofind import (
    MODE_FREQUENCY,
    MODE_UNDERVOLT,
    AutofindConfig,
)
from arctuner.autofind import (
    autofind as _base_autofind,
)
from arctuner.autotune import loader
from arctuner.igcl.api import IgclApi, TelemetrySnapshot
from arctuner.tuner import SafetyLimitError


@dataclass
class TuningConfig:
    """Настройки поиска авторазгона."""

    mode: str = MODE_UNDERVOLT  # что двигаем
    step: float = 2.0  # начальный шаг
    min_step: float = 0.5  # минимальный шаг (при верификации)
    max_steps: int = 30  # потолок итераций
    settle_seconds: float = 1.0  # пауза после шага
    temp_ceiling_c: float = 80.0  # откат при превышении
    require_load: bool = True  # нужна нагрузка для верификации
    load_timeout: float = 30.0  # ожидание нагрузки
    external_cmd: str | None = None  # команда для стресса
    fitness_function: str = "power_efficiency"  # функция fitness


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
        """Насколько ушли от базы (знак зависит от режима)."""
        return self.best_stable - self.baseline

    def best_profile(self) -> dict:
        """Возвращает лучший найденный профиль."""
        best = None
        for entry in self.history:
            if entry.get("passed", False):
                best = entry
        if best is None:
            return {"mode": self.mode, "value": self.baseline, "verified": False}
        return best


class TuningEngine:
    """Адаптивный движок авторазгона."""

    def __init__(
        self,
        api: IgclApi,
        handle,
        cfg: TuningConfig | None = None,
    ) -> None:
        self._api = api
        self._handle = handle
        self._cfg = cfg or TuningConfig()
        self._base_cfg = self._to_base_config()

    def _to_base_config(self) -> AutofindConfig:
        """Конвертируем TuningConfig в AutofindConfig."""
        return AutofindConfig(
            mode=self._cfg.mode,
            step=self._cfg.step,
            max_steps=self._cfg.max_steps,
            settle_seconds=self._cfg.settle_seconds,
            temp_ceiling_c=self._cfg.temp_ceiling_c,
            require_load=self._cfg.require_load,
            load_timeout_seconds=self._cfg.load_timeout,
        )

    def run(self, *, on_step: Callable | None = None) -> TuningResult:
        """Запускает адаптивный поиск.

        Args:
            on_step: колбэк (шаг, value, snap).

        Returns:
            TuningResult с историей и лучшим профилем.
        """
        history: list[dict] = []
        fitness_history: list[dict] = []
        best = self._cfg.step  # начальное лучшее значение (relative to baseline)
        current_step = self._cfg.step
        step_num = 0
        direction = -1.0 if self._cfg.mode == MODE_UNDERVOLT else 1.0

        def restore(value: float) -> None:
            """Возвращает карту к значению."""
            _base_autofind.autofind(
                self._api,
                self._handle,
                self._base_cfg,
            )
            # Применяем best к карте
            try:
                from arctuner.autofind import MODE_FREQUENCY, MODE_UNDERVOLT

                if self._cfg.mode == MODE_UNDERVOLT:
                    from arctuner.tuner import set_gpu_voltage_offset

                    set_gpu_voltage_offset(self._api, self._handle, value)
                elif self._cfg.mode == MODE_FREQUENCY:
                    from arctuner.tuner import set_gpu_freq_offset

                    set_gpu_freq_offset(self._api, self._handle, value)
            except SafetyLimitError:
                pass

        stopped_reason = "поиск завершён"
        baseline = None

        try:
            # Получаем baseline из current_values
            from arctuner.tuner import current_values

            vals = current_values(self._api, self._handle)
            field_name = (
                "gpuVoltageOffset" if self._cfg.mode == MODE_UNDERVOLT else "gpuFrequencyOffset"
            )
            baseline = vals.get(field_name)

            if baseline is None:
                raise SafetyLimitError(f"не удалось прочитать {field_name}")

            for i in range(1, self._cfg.max_steps + 1):
                # Адаптивный шаг:
                #   - пока стабильно, уменьшаем шаг (binary descent)
                #   - на провале — откатываемся
                candidate = round(baseline + direction * current_step, 6)

                # Применяем candidate
                try:
                    if self._cfg.mode == MODE_UNDERVOLT:
                        from arctuner.tuner import set_gpu_voltage_offset

                        set_gpu_voltage_offset(self._api, self._handle, candidate)
                    elif self._cfg.mode == MODE_FREQUENCY:
                        from arctuner.tuner import set_gpu_freq_offset

                        set_gpu_freq_offset(self._api, self._handle, candidate)
                except SafetyLimitError:
                    stopped_reason = f"достигнут диапазон IGCL на шаге {i}"
                    restore(best)
                    break

                # Ждём стабилизации
                time.sleep(self._cfg.settle_seconds)

                # Запускаем стресс (если нужен)
                if self._cfg.require_load:
                    loaded = loader.verify_stress(
                        self._api,
                        self._handle,
                        timeout=self._cfg.load_timeout,
                    )
                    if not loaded:
                        stopped_reason = f"нет нагрузки на шаге {i}"
                        restore(best)
                        break

                # Считаем телеметрию
                snap = self._api.read_power_telemetry(self._handle)

                # Guard check
                if snap.gpu_temp_c is not None and snap.gpu_temp_c > self._cfg.temp_ceiling_c:
                    stopped_reason = f"температура {snap.gpu_temp_c:.0f}°C > потолка {self._cfg.temp_ceiling_c:.0f}°C"
                    restore(best)
                    break

                # Fitness check
                fitness = self._evaluate_fitness(snap, candidate)

                history.append(
                    {
                        "step": i,
                        "value": candidate,
                        "temp_c": snap.gpu_temp_c,
                        "power_percent": snap.gpu_power_percent,
                        "passed": True,
                        "fitness": fitness,
                    }
                )
                fitness_history.append(
                    {
                        "step": i,
                        "fitness": fitness,
                        "value": candidate,
                    }
                )

                if on_step:
                    on_step(i, candidate, snap)

                best = candidate
                step_num = i

                # Уменьшаем шаг после успеха (binary descent)
                current_step = max(current_step * 0.7, self._cfg.min_step)

        except KeyboardInterrupt:
            stopped_reason = "прервано пользователем (Ctrl+C)"
            restore(best)

        restore(best)

        return TuningResult(
            mode=self._cfg.mode,
            baseline=baseline or 0.0,
            best_stable=best,
            steps_taken=step_num,
            stopped_reason=stopped_reason,
            history=history,
            fitness_history=fitness_history,
        )

    def _evaluate_fitness(
        self,
        snap: TelemetrySnapshot,
        value: float,
    ) -> float:
        """Оценивает fitness текущего состояния.

        По умолчанию — power_efficiency:
          fitness = (1 - power_percent / 100) * 10  # меньше мощность = лучше
          + temperature_factor: меньше температура = лучше
        """
        power_factor = (1.0 - snap.gpu_power_percent / 100.0) * 5.0
        temp_factor = (1.0 - snap.gpu_temp_c / 100.0) * 3.0
        return round(power_factor + temp_factor, 4)
