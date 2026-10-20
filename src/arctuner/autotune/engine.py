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

import contextlib
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
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

    def field_grid(self, field: str) -> tuple[float, float] | None:
        """Сетка IGCL поля (min, step) — кандидаты приводятся к ней перед записью.

        None — сетка неизвестна/не поддерживается (движок не приводит).
        """
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

    def validate(self) -> None:
        """Проверяет конфигурацию ДО любых обращений к карте.

        Профили — JSON: там легко получить строку вместо числа, NaN или
        неизвестный режим. Без этой проверки такие значения падают TypeError'ом
        уже в рабочем потоке, мимо всех каналов ошибок GUI.

        Raises:
            SafetyLimitError: значение некорректно (карта не тронута).
        """
        if self.mode not in (MODE_FREQUENCY, MODE_UNDERVOLT):
            raise SafetyLimitError(f"неизвестный режим автопоиска: {self.mode!r}")
        numeric = {
            "step": self.step,
            "min_step": self.min_step,
            "settle_seconds": self.settle_seconds,
            "temp_ceiling_c": self.temp_ceiling_c,
            "load_timeout": self.load_timeout,
        }
        for name, value in numeric.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SafetyLimitError(f"{name} — число ожидается, получено: {value!r}")
            if not math.isfinite(value):
                raise SafetyLimitError(f"{name} — бесконечность/NaN недопустимы")
        if self.step <= 0 or self.min_step <= 0:
            raise SafetyLimitError(f"step/min_step должны быть > 0: {self.step}/{self.min_step}")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int):
            raise SafetyLimitError(f"max_steps — целое ожидается, получено: {self.max_steps!r}")
        if self.max_steps <= 0:
            raise SafetyLimitError(f"max_steps должен быть > 0: {self.max_steps}")
        if self.load_timeout < 0 or self.settle_seconds < 0 or self.temp_ceiling_c <= 0:
            raise SafetyLimitError("load_timeout/settle_seconds >= 0, temp_ceiling_c > 0")
        if self.external_cmd is not None and not isinstance(self.external_cmd, str):
            raise SafetyLimitError(f"external_cmd — строка/None: {self.external_cmd!r}")


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
    card_value: float | None = None
    """Фактическое подтверждённое значение на карте к концу поиска.

    None — откат не удался (драйвер отверг даже сброс): состояние неизвестно,
    вкладка обязана предупредить, а не «поздравить» с результатом.
    """

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

    def _sleep(
        self,
        seconds: float,
        stop_check: Callable[[], bool] | None,
        sample: Callable[[], None] | None = None,
    ) -> None:
        """Пауза, прерываемая stop_check.

        sample зовётся примерно раз в 0.5 с: живой график GUI не «замирает»
        на время settle (раньше снимки приходили только после шага).
        """
        deadline = time.monotonic() + seconds
        next_sample = time.monotonic()
        while True:
            if stop_check is not None and stop_check():
                return
            now = time.monotonic()
            if sample is not None and now >= next_sample:
                sample()
                next_sample = now + 0.5
            left = deadline - now
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    def _read_telemetry(
        self, on_sample: Callable[[TelemetrySnapshot], None] | None
    ) -> TelemetrySnapshot:
        """Читает телеметрию и (если задан) отдаёт снимок живому графику.

        Гарды используют ровно этот же снимок — подписчик видит те значения,
        по которым движок принимает решение, без второго/расходящегося чтения.
        """
        snap = self._ctx.telemetry()
        if on_sample is not None:
            on_sample(snap)
        return snap

    def _apply(self, value: float) -> None:
        """Применяет значение через контекст (двухуровневая проверка + журнал)."""
        if self._cfg.mode == MODE_FREQUENCY:
            self._ctx.set_freq_offset(value)
        elif self._cfg.mode == MODE_UNDERVOLT:
            self._ctx.set_voltage_offset(value)
        else:
            raise SafetyLimitError(f"неизвестный режим автопоиска: {self._cfg.mode}")

    def _restore(self, value: float) -> bool:
        """Гарантированный откат к значению.

        Последняя попытка при невозможности шага — полный сброс к дефолту,
        чтобы не оставить полузадранное состояние (как в autofind.py).
        Сбой журнала (``JournalWriteError`` — это ``OSError``) не должен
        вылетать из finally и оставлять внешнюю нагрузку запущенной.

        Returns:
            False — карта НЕ осталась на value (сброшена к дефолту либо сброс
            тоже не удался): состояние неизвестно, итог сообщит честно.
        """
        try:
            self._apply(value)
            return True
        except (IgclError, SafetyLimitError, OSError):
            pass
        with contextlib.suppress(IgclError, SafetyLimitError, OSError):
            self._ctx.reset_oc()
        return False

    def run(
        self,
        *,
        on_step: Callable[[int, float, TelemetrySnapshot], None] | None = None,
        stop_check: Callable[[], bool] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_sample: Callable[[TelemetrySnapshot], None] | None = None,
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
            on_sample: колбэк (снимок) на КАЖДОЕ чтение телеметрии движком,
                включая settle и ожидание нагрузки: живой график GUI. Это не
                отдельный опрос — те же значения, по которым работают гарды.

        Returns:
            TuningResult с историей и лучшим стабильным значением.

        Raises:
            SafetyLimitError: сессия не открыта / исходное значение не читается.
            IgclError: ошибка драйвера (после отката к best).
            OSError: сбой запуска/остановки внешнего стресс-процесса.
        """
        cfg = self._cfg
        ctx = self._ctx
        cfg.validate()  # до любых обращений к карте: битый профиль не должен её трогать
        field_name = "gpuVoltageOffset" if cfg.mode == MODE_UNDERVOLT else "gpuFrequencyOffset"
        # Сетка IGCL (min, step): кандидаты приводятся к ней, иначе tuner
        # отклоняет запись («не кратен шагу») — движок обрывался на 3-м шаге.
        grid = ctx.field_grid(field_name)

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
        # Что подтверждённо на карте к концу поиска. best — пока записи шли
        # штатно; None — откат не удался (драйвер отверг и сброс): вкладка
        # обязана предупредить, а не «поздравить» с результатом.
        card_value: float | None = best
        stress: Any | None = None  # внешний процесс нагрузки

        if on_status:
            on_status(f"старт: база {baseline:g}, шаг {cfg.step:g} {direction:+.0f}")

        # Единый источник снимков: подписчик получает именно те значения, по
        # которым движок проверяет нагрузку, температуру и watchdog.
        sample = partial(self._read_telemetry, on_sample) if on_sample is not None else None
        if sample is not None:
            sample()  # первый снимок сразу — график не пустует до первого шага

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
                        card_value=baseline,  # карта не тронута
                    )

            for i in range(1, cfg.max_steps + 1):
                if stop_check is not None and stop_check():
                    reason = "остановлено пользователем — откат к стабильному"
                    break  # карта на best (need_restore=False): откат не нужен

                # candidate считается ОТ BEST: стабильные шаги накапливаются,
                # а не нанизываются мелкими от baseline.
                candidate = round(best + direction * current_step, 6)
                if grid is not None:
                    lo, gs = grid
                    snapped = round(lo + round((candidate - lo) / gs) * gs, 6)
                    # Не только равенство: baseline мог быть вне сетки (значение
                    # выставлено сторонним ПО), и округление увело бы шаг НАЗАД
                    # от best — в frequency это запись НИЖЕ исходного.
                    if (direction > 0 and snapped <= best) or (direction < 0 and snapped >= best):
                        reason = "шаг мельче сетки IGCL (кратность) — граница найдена"
                        break
                    candidate = snapped

                # Флаг ПЕРЕД записью: любое исключение между apply и засчётом
                # шага (IgclError из телеметрии, сбой колбэка…) обязано откатить
                # карту с необработанного кандидата к best.
                need_restore = True
                try:
                    self._apply(candidate)
                except SafetyLimitError as e:
                    # Запись не прошла — карта осталась на best, откат не нужен.
                    need_restore = False
                    reason = f"достигнут диапазон IGCL на шаге {i}: {e}"
                    if cfg.mode == MODE_UNDERVOLT:
                        reason += (
                            " — undervolt через смещение на B580 ограничен (min=0);"
                            " используйте кривую V/F"
                        )
                    break

                if cfg.settle_seconds > 0:
                    self._sleep(cfg.settle_seconds, stop_check, sample)
                    # Проверяем только после реальной паузы: без settle отмена
                    # будет замечена на следующей итерации, и лишний вызов
                    # stop_check не сдвигает ожидания тестов/UI.
                    if stop_check is not None and stop_check():
                        reason = "остановлено пользователем — откат к стабильному"
                        break

                if cfg.require_load:
                    if on_status:
                        on_status(f"шаг {i}: применено {candidate:+g} — ожидание нагрузки…")
                    under_load = loader.verify_stress(
                        # sample уже отдаёт снимок графику; без подписчика —
                        # обычное чтение контекста.
                        sample if sample is not None else ctx.telemetry,
                        interval=0.5,
                        timeout=cfg.load_timeout,
                        stop_check=stop_check,
                    )
                    if stop_check is not None and stop_check():
                        reason = "остановлено пользователем — откат к стабильному"
                        break
                    if not under_load:
                        reason = f"нет нагрузки на шаге {i} — стабильность не подтверждена"
                        break
                elif on_status:
                    on_status(f"шаг {i}: применено {candidate:+g} — замер телеметрии…")

                snap = self._read_telemetry(on_sample)

                if self._watchdog is not None:
                    evt = self._watchdog.check(snap)
                    if evt is not None:
                        reason = f"watchdog: {evt.type} — {evt.detail}"
                        break

                if snap.gpu_temp_c is not None and snap.gpu_temp_c > cfg.temp_ceiling_c:
                    reason = (
                        f"температура {snap.gpu_temp_c:.0f}°C > потолка {cfg.temp_ceiling_c:.0f}°C"
                    )
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

                best = candidate
                steps_taken = i
                need_restore = False  # шаг засчитан — карта на best, откат не нужен

                if on_step:
                    on_step(i, candidate, snap)

                current_step = round(current_step * self.STEP_DECAY, 6)
                if current_step < cfg.min_step:
                    reason = "шаг ниже минимального — граница найдена"
                    break

        except KeyboardInterrupt:
            # need_restore уже корректен по фазе шага (True — если карта ушла
            # с best и ещё не засчитана) — finally выполнит откат.
            reason = "прервано пользователем (Ctrl+C) — выполнен откат"
            from arctuner.autofind import AutofindAbortError

            raise AutofindAbortError(reason) from None
        finally:
            # Откат и остановка нагрузчика независимы: сбой журнала/отката
            # (в т.ч. JournalWriteError) не имеет права оставить стресс-процесс
            # запущенным. Исходное исключение при этом не теряется.
            try:
                if need_restore:
                    # Ошибка отката не перекрывает исходное исключение/результат:
                    # _restore сам пробует полный сброс OC, а если и это не вышло —
                    # card_value=None и честная причина в результате.
                    restored = False
                    try:
                        restored = self._restore(best)
                    except (IgclError, SafetyLimitError, OSError):
                        restored = False
                    card_value = best if restored else None
                    if not restored:
                        reason = f"{reason} (состояние карты не подтверждено: откат не удался)"
                else:
                    card_value = best  # карта штатно на последнем стабильном
            finally:
                if stress is not None:
                    # Остановка нагрузки не должна перекрывать результат/исключение.
                    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                        loader.terminate_stress(stress)

        return TuningResult(
            mode=cfg.mode,
            baseline=baseline,
            best_stable=best,
            steps_taken=steps_taken,
            stopped_reason=reason,
            history=history,
            fitness_history=fitness_history,
            card_value=card_value,
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
