"""Тесты пакета autotune: движок, watchdog, нагрузка, профили.

Железо не требуется: GpuContext подменяется фейком с теми же методами
(записи проходят через tuner-модель поведения — выброс SafetyLimitError
вне диапазона). Нагрузочные паузы сжаты до миллисекунд.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from arctuner.autotune import loader
from arctuner.autotune import watchdog as wd_mod
from arctuner.autotune.engine import TuningConfig, TuningEngine, TuningResult
from arctuner.autotune.profiles import ProfileStore
from arctuner.autotune.watchdog import Watchdog, WatchdogEvent
from arctuner.igcl.api import TelemetrySnapshot
from arctuner.tuner import SafetyLimitError

FREQ_MIN = -300.0
FREQ_MAX = 1000.0


def _snap(
    *,
    activity: int = 100,
    temp: float | None = 70.0,
    power: float | None = 50.0,
    energy: float = 1000.0,
) -> TelemetrySnapshot:
    """Снимок телеметрии только из полей, которые читают тестовые слои."""
    return TelemetrySnapshot(
        gpu_voltage_v=None,
        gpu_freq_mhz=None,
        gpu_temp_c=temp,
        gpu_power_percent=power,
        vram_voltage_v=None,
        vram_eff_freq_mbps=None,
        vram_temp_c=None,
        power_limited=False,
        temperature_limited=False,
        current_limited=False,
        voltage_limited=False,
        utilization_limited=False,
        activity_counter=activity,
        gpu_energy_counter_j=energy,
    )


class FakeCtx:
    """GpuContext-подобный фейк: журнал заменён списком записей."""

    def __init__(self, *, temp: float = 70.0, activity: int = 100) -> None:
        self.values: dict[str, float | None] = {
            "gpuFrequencyOffset": 0.0,
            "gpuVoltageOffset": 0.0,
        }
        self.applied: list[float] = []  # значения, реально попавшие на карту
        self.journal: list[float] = []  # все записи (включая откаты)
        self.temp_c = temp
        self.activity = activity
        self.reset_count = 0
        self.grid: tuple[float, float] | None = None  # (min, step) IGCL; None — без сетки

    def current_values(self) -> dict[str, float | None]:
        return dict(self.values)

    def field_grid(self, field: str) -> tuple[float, float] | None:
        """Сетка IGCL: по умолчанию не ограничена (тесты сетки задают явно)."""
        return self.grid

    def set_freq_offset(self, mhz: float) -> object:
        if not FREQ_MIN <= mhz <= FREQ_MAX:
            raise SafetyLimitError(f"{mhz:g} вне [{FREQ_MIN:g}, {FREQ_MAX:g}]")
        self.values["gpuFrequencyOffset"] = mhz
        self.applied.append(mhz)
        self.journal.append(mhz)
        return object()

    def set_voltage_offset(self, percent: float) -> object:
        if percent < 0:  # поведение B580: диапазон min=0
            raise SafetyLimitError(f"{percent:g} вне [0, …] — undervolt ограничен")
        self.values["gpuVoltageOffset"] = percent
        self.applied.append(percent)
        self.journal.append(percent)
        return object()

    def telemetry(self) -> TelemetrySnapshot:
        return _snap(activity=self.activity, temp=self.temp_c)

    def reset_oc(self) -> object:
        self.reset_count += 1
        return object()


def _cfg(**over: object) -> TuningConfig:
    """Быстрый конфиг: без пауз и без нагрузки, частота."""
    base = {
        "mode": "frequency",
        "step": 50.0,
        "min_step": 10.0,
        "max_steps": 8,
        "settle_seconds": 0.0,
        "temp_ceiling_c": 80.0,
        "require_load": False,
        "load_timeout": 0.0,
    }
    base.update(over)  # type: ignore[arg-type]
    return TuningConfig(**base)  # type: ignore[arg-type]


class TestEngineFrequency:
    """Адаптивный спуск: накопление от best, уменьшение шага, границы."""

    def test_descends_and_accumulates_from_best(self) -> None:
        """Кандидаты накапливаются от best (не от baseline), шаг ×0.8."""
        ctx = FakeCtx()
        engine = TuningEngine(ctx, _cfg(step=50.0, min_step=10.0, max_steps=3))
        result = engine.run()
        # candidates: +50 → (шаг 40) +90 → (шаг 32) +122 (частота — вверх)
        assert result.steps_taken == 3
        assert result.best_stable == 122.0
        assert [e["value"] for e in result.history] == [50.0, 90.0, 122.0]
        assert result.stopped_reason == "достигнут лимит шагов"
        assert ctx.values["gpuFrequencyOffset"] == 122.0  # карта на best

    def test_min_step_boundary_finishes_early(self) -> None:
        """Убывающий шаг дожимает поиск до порога min_step."""
        ctx = FakeCtx()
        engine = TuningEngine(ctx, _cfg(step=50.0, min_step=30.0, max_steps=50))
        result = engine.run()
        # шаги: 50→40→32 → 25.6 < 30: стоп после трёх успешных шагов
        assert result.steps_taken == 3
        assert "шаг ниже минимального" in result.stopped_reason

    def test_limits_of_igcl_range_rollback(self) -> None:
        """Провалившийся кандидат не записан; карта осталась на стабильном.

        Неудачный apply атомарен (IGCL отклоняет без записи) — откат не нужен,
        лишней записи на карту/в журнал быть не должно.
        """
        ctx = FakeCtx()
        engine = TuningEngine(
            ctx, _cfg(step=600.0, min_step=50.0, max_steps=8)
        )  # +600 ok → (шаг 480) +1080 — за потолком 1000
        result = engine.run()
        assert result.steps_taken == 1
        assert result.best_stable == 600.0
        assert ctx.applied == [600.0]  # только успех; откат не нужен — карта на best
        assert 1080.0 not in ctx.applied
        assert "диапазон IGCL" in result.stopped_reason

    def test_stop_check_graceful_rollback(self) -> None:
        """stop_check=True → откат к последнему стабильному, не к кандидату."""
        ctx = FakeCtx()
        calls = {"n": 0}

        def stop() -> bool:
            calls["n"] += 1
            return calls["n"] > 2  # после двух успешных шагов

        engine = TuningEngine(ctx, _cfg())
        result = engine.run(stop_check=stop)
        assert result.steps_taken == 2
        assert result.best_stable == 90.0
        assert ctx.applied[-1] == 90.0
        assert "остановлено пользователем" in result.stopped_reason

    def test_temp_guard_rolls_back_to_best(self) -> None:
        """Температура выше потолка — откат best ещё до принятия шага."""
        ctx = FakeCtx(temp=90.0)
        engine = TuningEngine(ctx, _cfg())
        result = engine.run()
        assert result.steps_taken == 0
        assert result.best_stable == 0.0
        assert ctx.applied[-1] == 0.0  # откат к базе провалившегося +50
        assert "температура" in result.stopped_reason

    def test_undervolt_blocked_with_hint(self) -> None:
        """Undervolt смещением на B580 честно стопорится с подсказкой про V/F."""
        ctx = FakeCtx()
        engine = TuningEngine(ctx, _cfg(mode="undervolt"))
        result = engine.run()
        assert result.steps_taken == 0
        assert "кривую V/F" in result.stopped_reason

    def test_fitness_none_when_sensors_missing(self) -> None:
        """Бездатчиковый снимок: fitness None (числа не придумываются)."""
        ctx = FakeCtx()
        ctx.telemetry = lambda: _snap(temp=None, power=None)  # type: ignore[method-assign]
        result = TuningEngine(ctx, _cfg()).run()
        assert result.history[0]["fitness"] is None

    def test_result_shape(self) -> None:
        """Контракт TuningResult: improvement, best_profile."""
        ctx = FakeCtx()
        result: TuningResult = TuningEngine(ctx, _cfg(step=100.0, max_steps=1)).run()
        assert result.improvement == 100.0
        profile = result.best_profile()
        assert profile["passed"] is True
        assert profile["value"] == 100.0

    def test_status_reports_start_and_load_wait(self) -> None:
        """on_status: старт-статус всегда; при require_load — ожидание нагрузки."""
        ctx = FakeCtx()
        statuses: list[str] = []
        TuningEngine(ctx, _cfg(require_load=True)).run(on_status=statuses.append)
        assert statuses, "нет статусов — UI будет выглядеть зависшим"
        assert statuses[0].startswith("старт")
        assert any("ожидание нагрузки" in s for s in statuses)

    def test_status_without_load(self) -> None:
        """Без требования нагрузки статусы переходят к замеру телеметрии."""
        ctx = FakeCtx()
        statuses: list[str] = []
        TuningEngine(ctx, _cfg(require_load=False, max_steps=1)).run(on_status=statuses.append)
        assert statuses[0].startswith("старт")
        assert any("замер телеметрии" in s for s in statuses)


class TestExternalCmd:
    """Внешний нагрузчик: запуск при конфиге и остановка после поиска."""

    def test_unstartable_cmd_yields_honest_result(self, monkeypatch) -> None:
        """Не стартует нагрузчик → результат с честной причиной, записи нет."""
        monkeypatch.setattr(loader, "start_external", lambda cmd: None)
        monkeypatch.setattr(loader, "terminate_stress", lambda proc: None)
        ctx = FakeCtx()
        result = TuningEngine(ctx, _cfg(external_cmd="missing_bench.exe")).run()
        assert result.steps_taken == 0
        assert result.best_stable == 0.0
        assert "стресс-нагрузчик" in result.stopped_reason
        assert ctx.applied == []  # карта не тронута


class TestWatchdog:
    """События watchdog: авария температуры, заморозка, TDR."""

    @pytest.fixture(autouse=True)
    def _no_real_powershell(self, monkeypatch) -> None:
        """Все проверки — с подменённым Get-WinEvent (без внешних вызовов)."""

        class _Empty:
            stdout = b""

        monkeypatch.setattr(wd_mod.subprocess, "run", lambda *a, **k: _Empty(), raising=True)

    def test_temperature_emergency(self) -> None:
        w = Watchdog()
        evt = w.check(_snap(temp=86.0))
        assert evt is not None and evt.type == "TEMP_EMERGENCY"

    def test_temperature_ok(self) -> None:
        assert Watchdog().check(_snap(temp=84.9)) is None

    def test_frozen_telemetry(self) -> None:
        """Активность была, но стоит; энергия не растёт → сбой драйвера."""
        w = Watchdog()
        assert w.check(_snap(activity=50, energy=1000.0)) is None  # база
        evt = w.check(_snap(activity=50, energy=1000.0))
        assert evt is not None and evt.type == "TELEMETRY_FROZEN"

    def test_frozen_not_triggered_when_activity_grows(self) -> None:
        w = Watchdog()
        w.check(_snap(activity=50, energy=1000.0))
        evt = w.check(_snap(activity=60, energy=1005.0))
        assert evt is None

    def test_frozen_not_triggered_on_idle_gpu(self) -> None:
        """Простой (activity==0) — законная «заморозка», не инцидент."""
        w = Watchdog()
        w.check(_snap(activity=0, energy=500.0))
        assert w.check(_snap(activity=0, energy=500.0)) is None

    def test_tdr_detected_after_start(self, monkeypatch) -> None:
        """Event 4101 ПОСЛЕ запуска watchdog → событие (провайдер Display)."""

        class FakeCompleted:
            stdout = b"2026-10-19T20:15:00\n"

        def fake_run(*_args: object, **_kwargs: object) -> FakeCompleted:
            return FakeCompleted()

        monkeypatch.setattr(wd_mod.subprocess, "run", fake_run)
        w = Watchdog()
        evt = w.check(_snap())
        assert evt is not None and evt.type == "TDR"

    def test_tdr_ignores_old_events(self, monkeypatch) -> None:
        """StartTime-фильтр: события только после старта watchdog."""

        class FakeCompleted:
            stdout = b""  # до старта ничего — PowerShell вернул пусто

        monkeypatch.setattr(wd_mod.subprocess, "run", lambda *a, **k: FakeCompleted())
        assert Watchdog().check(_snap()) is None

    def test_tdr_powershell_uses_starttime_filter(self, monkeypatch) -> None:
        """В запросе фигурируют System/4101/StartTime (не «списать прошлые дни»)."""
        captured: dict[str, object] = {}

        class FakeCompleted:
            stdout = b""

        def fake_run(cmd, **kwargs: object) -> FakeCompleted:
            captured["cmd"] = " ".join(cmd)
            captured["kwargs"] = kwargs
            return FakeCompleted()

        monkeypatch.setattr(wd_mod.subprocess, "run", fake_run)
        Watchdog().check(_snap())
        cmd = str(captured["cmd"])
        assert "Id=4101" in cmd
        assert "LogName='System'" in cmd
        assert "StartTime" in cmd

    def test_tdr_query_provider_and_culture_free_time(self, monkeypatch) -> None:
        """ProviderName='Display' + числовой конструктор времени (culture-free).

        [datetime]'ISO-строка' парсится не на всех локалях (не-Григорианские
        календари молча убивают детектор); провайдер в фильтре отсекает 4101
        от чужих устройств.
        """
        captured: dict[str, object] = {}

        class FakeCompleted:
            stdout = b""

        def fake_run(cmd, **kwargs: object) -> FakeCompleted:
            captured["cmd"] = " ".join(cmd)
            return FakeCompleted()

        monkeypatch.setattr(wd_mod.subprocess, "run", fake_run)
        Watchdog().check(_snap())
        cmd = str(captured["cmd"])
        assert "ProviderName='Display'" in cmd
        assert "[datetime]::new(" in cmd

    def test_tdr_negative_cached(self, monkeypatch) -> None:
        """Негатив кэшируется: PowerShell не спавнится на каждом шаге."""
        w = Watchdog()
        calls = {"n": 0}

        def fake_query() -> list[str]:
            calls["n"] += 1
            return []

        monkeypatch.setattr(w, "_query_tdr_events", fake_query)
        assert w.check(_snap(activity=100)) is None
        assert w.check(_snap(activity=101)) is None  # активность растёт — не заморозка
        assert calls["n"] == 1  # повтор в интервале пропущен кэшем
        monkeypatch.setattr(wd_mod.time, "monotonic", lambda: 1e9)
        assert w.check(_snap(activity=102)) is None
        assert calls["n"] == 2  # интервал прошёл — снова опрос

    def test_counter_reset_is_incident(self, monkeypatch) -> None:
        """Сброс activity_counter (рестарт драйвера) — инцидент, не «заморозка».

        После TDR оба счётчика обнуляются: энергия «скачет» и ложная
        заморозка не детектится — ловим именно уменьшение счётчика.
        """
        monkeypatch.setattr(Watchdog, "_query_tdr_events", lambda self: [])
        w = Watchdog()
        assert w.check(_snap(activity=100, energy=5000.0)) is None
        evt = w.check(_snap(activity=40, energy=2000.0))
        assert evt is not None and evt.type == "TELEMETRY_FROZEN"
        assert "сброшен" in evt.detail

    def test_subprocess_failure_not_incident(self, monkeypatch) -> None:
        """Нет PowerShell/таймаут — не инцидент (не наш сбой)."""

        def fake_run(*_a: object, **_k: object) -> None:
            raise FileNotFoundError

        monkeypatch.setattr(wd_mod.subprocess, "run", fake_run)
        assert Watchdog().check(_snap()) is None

    def test_event_dataclass(self) -> None:
        evt = WatchdogEvent("TDR", "x")
        assert evt.type == "TDR"


class TestLoader:
    """Проверка нагрузки по activity_counter (дельта > 0)."""

    def test_activity_grows(self) -> None:
        snaps = iter([_snap(activity=10), _snap(activity=10), _snap(activity=42)])
        assert loader.verify_stress(lambda: next(snaps), interval=0.01, timeout=1.0)

    def test_activity_flat_times_out(self) -> None:
        """Постоянная активность (без дельты) — ожидание истекает, False."""
        result = loader.verify_stress(lambda: _snap(activity=7), interval=0.01, timeout=0.05)
        assert result is False

    def test_zero_timeout_single_probe(self) -> None:
        snaps = iter([_snap(activity=5), _snap(activity=9)])
        assert loader.verify_stress(lambda: next(snaps), timeout=0.0)

    def test_start_external_unsafe_fails_quietly(self, monkeypatch) -> None:
        """start_external: OSError при старте → None (движок выдаёт свой вердикт)."""

        def fake_popen(*_args: object, **_kwargs: object) -> None:
            raise OSError("not found")

        monkeypatch.setattr(loader.subprocess, "Popen", fake_popen)
        assert loader.start_external("anything") is None

    def test_terminate_stress_uses_taskkill_on_windows(self, monkeypatch) -> None:
        """Windows: завершение дети+родители — taskkill /T /F; затем wait."""
        monkeypatch.setattr(loader.sys, "platform", "win32")
        captured: dict[str, object] = {}

        class FakeProc:
            def __init__(self) -> None:
                self.pid = 4242
                self.waited = False

            def wait(self, timeout: float = 5.0) -> int:
                self.waited = True
                return 0

        def fake_run(cmd, **kwargs: object) -> None:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        monkeypatch.setattr(loader.subprocess, "run", fake_run)

        proc = FakeProc()
        loader.terminate_stress(proc)
        assert proc.waited
        assert captured["cmd"] == ["taskkill", "/F", "/T", "/PID", "4242"]
        assert captured["kwargs"].get("capture_output") is True
        # Окно консоли вспыхивать из GUI не должно (win32: CREATE_NO_WINDOW).
        assert captured["kwargs"].get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def test_tdr_query_runs_hidden(self, monkeypatch) -> None:
        """PowerShell TDR-запрос запускается без вспыхивающей консоли (GUI)."""
        captured: dict[str, object] = {}

        class _Empty:
            stdout = b""

        def fake_run(cmd, **kwargs: object) -> _Empty:
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _Empty()

        monkeypatch.setattr(wd_mod.subprocess, "run", fake_run)
        Watchdog()._query_tdr_events()
        assert captured["kwargs"].get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags = captured["cmd"]
        assert isinstance(flags, list) and "-NoProfile" in flags and "-NonInteractive" in flags


class TestProfiles:
    """Хранилище профилей: чтение/запись по фактическому пути."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = ProfileStore(directory=tmp_path / "p")
        data = {"mode": "frequency", "step": 15.0}
        saved = store.save(tmp_path / "p" / "q.json", data)
        assert saved.exists()
        assert store.load(saved) == data

    def test_load_non_dict_raises(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError):
            store.load(bad)

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ProfileStore(tmp_path).load(tmp_path / "absent.json")

    def test_save_writes_utf8_json(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path)
        store.save(tmp_path / "a.json", {"external_cmd": "бен"})
        content = (tmp_path / "a.json").read_text(encoding="utf-8")
        assert "бен" in content  # ensure_ascii=False: не \u-эскейпы
        json.loads(content)  # корректный JSON


class TestConfigValidate:
    """validate(): битый профиль (JSON) не должен трогать карту.

    Профили — обычный JSON: там легко получить строку вместо числа, NaN
    или неизвестный режим. Раньше такие значения падали TypeError'ом уже
    в рабочем потоке, мимо каналов ошибок GUI.
    """

    @pytest.mark.parametrize(
        "over",
        [
            {"mode": "turbo"},  # неизвестный режим (раньше → reset_oc, потеря OC)
            {"step": "15"},  # строка вместо числа
            {"step": -15.0},  # инверсия направления
            {"step": float("nan")},  # NaN из JSON
            {"max_steps": 0},  # пустой цикл
            {"max_steps": "30"},  # строка вместо int
            {"min_step": 0.0},  # бесконечный спуск
            {"temp_ceiling_c": -5.0},  # гард выключен
            {"load_timeout": -1.0},
            {"external_cmd": 123},  # не строка
        ],
    )
    def test_invalid_config_rejected_before_card_touch(self, over: dict) -> None:
        ctx = FakeCtx()
        with pytest.raises(SafetyLimitError):
            TuningEngine(ctx, _cfg(**over)).run()
        assert ctx.applied == []  # карта не тронута ни одной записью
        assert ctx.reset_count == 0  # и не сброшена к дефолту


class TestExceptionSafety:
    """Главный инвариант: карта не остаётся на необработанном кандидате."""

    def test_unexpected_exception_after_apply_rolls_back(self) -> None:
        """IgclError из телеметрии ПОСЛЕ apply → откат к best в finally."""
        from arctuner.igcl.api import IgclError

        ctx = FakeCtx()

        def boom() -> TelemetrySnapshot:
            raise IgclError(-1, "драйвер отвалился")

        ctx.telemetry = boom  # type: ignore[method-assign]
        with pytest.raises(IgclError):
            TuningEngine(ctx, _cfg(step=50.0, max_steps=1)).run()
        assert ctx.applied == [50.0, 0.0]  # кандидат записан → откат к baseline

    def test_stress_terminate_failure_not_fatal(self, monkeypatch) -> None:
        """Сбой остановки нагрузки не перекрывает результат/исключение."""

        def boom(proc: object) -> None:
            raise OSError("нет taskkill")

        monkeypatch.setattr(loader, "start_external", lambda cmd: object())
        monkeypatch.setattr(loader, "terminate_stress", boom)
        ctx = FakeCtx()
        result = TuningEngine(ctx, _cfg(external_cmd="bench.exe", max_steps=1)).run()
        assert result.steps_taken == 1  # результат дошёл целиком


class TestGridSnapping:
    """Кандидаты приводятся к сетке IGCL (кратность шагу от min).

    Регрессия: движок выдавал 36.6 при step=1.0 → tuner отклонял запись
    («не кратен шагу»), поиск обрывался с вводящим в заблуждение «диапазон».
    """

    def test_candidates_snap_to_grid(self) -> None:
        """Дробный кандидат округляется к сетке (36.6 → 37) — запись проходит."""
        ctx = FakeCtx()
        ctx.grid = (-300.0, 1.0)  # как на B580: min=-300, step=1.0 МГц
        result = TuningEngine(ctx, _cfg(step=15.0, min_step=3.0, max_steps=4)).run()
        # 15 → 27 → 36.6→37 → 44.68→45: без снаппинга шаг 3 отклонён бы
        assert result.steps_taken == 4
        assert result.best_stable == 45.0
        assert all(v == int(v) for v in ctx.applied)

    def test_step_below_grid_stops_as_boundary(self) -> None:
        """Шаг мельче сетки → «граница найдена», а не крутка до max_steps."""
        ctx = FakeCtx()
        ctx.grid = (-300.0, 50.0)  # крупная сетка: шаг 15 не даёт нового кратного
        result = TuningEngine(ctx, _cfg(step=15.0, max_steps=5)).run()
        assert result.steps_taken == 0
        assert "сетки IGCL" in result.stopped_reason
        assert ctx.applied == []  # ни одной записи — сетка не позволила шага
