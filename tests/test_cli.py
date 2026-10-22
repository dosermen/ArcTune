"""Тесты CLI без железа: парсер, doctor, версия и autofind через TuningEngine.

Autofind проверяется на фейковом GpuContext/движке: ни IGCL, ни Qt не нужны.
Главное — конфиг паритета с GUI и отсутствие «синтетической» записи журнала
с полями frequency/undervolt (журнал пишет сам движок реальными OC-полями).
"""

from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from arctuner import __version__
from arctuner.autotune import engine as engine_mod
from arctuner.autotune import watchdog as watchdog_mod
from arctuner.autotune.engine import TuningResult
from arctuner.cli import (
    _version,
    build_parser,
    cmd_autofind,
    cmd_doctor,
    cmd_fans,
    cmd_reset,
    cmd_set,
    main,
)
from arctuner.logbook import RESET_FANS_FIELD, RESET_OC_FIELD, Logbook, default_logbook_path


class _FakeLog:
    """Журнал-шпион: фиксирует имена полей, путь отдаёт как настоящий Logbook."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fields: list[str] = []

    def record(self, field: str, old: object, new: object, **kwargs: object) -> None:
        self.fields.append(field)


class _Info:
    name = "Intel Arc B580"


class FakeContext:
    """GpuContext-подобный контекст: open/close без IGCL."""

    last: FakeContext | None = None

    def __init__(self) -> None:
        self.info = _Info()
        self.log = _FakeLog(Path("fake-logbook.jsonl"))
        self.entered = False
        FakeContext.last = self

    def __enter__(self) -> FakeContext:
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.entered = False


class FakeWatchdog:
    def __init__(self, device: str = "") -> None:
        self.device = device


class FakeEngine:
    """Подменяет TuningEngine: запоминает конфиг/аргументы, отдаёт result."""

    last: FakeEngine | None = None
    result = TuningResult(
        mode="frequency",
        baseline=0.0,
        best_stable=45.0,
        steps_taken=3,
        stopped_reason="достигнут лимит шагов",
        card_value=45.0,
    )

    def __init__(self, ctx: object, cfg: object, *, watchdog: object = None) -> None:
        self.ctx = ctx
        self.cfg = cfg
        self.watchdog = watchdog
        self.run_kwargs: dict[str, object] = {}
        FakeEngine.last = self

    def run(self, **kwargs: object) -> TuningResult:
        self.run_kwargs = kwargs
        return self.result


@pytest.fixture()
def autofind_env(monkeypatch, tmp_path):
    """Подменяет контекст/движок/watchdog и изолирует журнал от пользователя."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    monkeypatch.setattr("arctuner.context.GpuContext", FakeContext)
    monkeypatch.setattr(engine_mod, "TuningEngine", FakeEngine)
    monkeypatch.setattr(watchdog_mod, "Watchdog", FakeWatchdog)
    FakeEngine.result = TuningResult(
        mode="frequency",
        baseline=0.0,
        best_stable=45.0,
        steps_taken=3,
        stopped_reason="достигнут лимит шагов",
        card_value=45.0,
    )


def _autofind(argv: list[str]) -> int:
    args = build_parser().parse_args(["autofind", "--yes", *argv])
    return cmd_autofind(args)


# --- парсер ---


def test_parser_autofind_defaults_to_frequency() -> None:
    args = build_parser().parse_args(["autofind"])
    assert args.mode == "frequency"
    assert args.step is None  # вычисляется в cmd_autofind по режиму


@pytest.mark.parametrize(
    ("argv", "watch", "replay"),
    [
        (["telemetry"], None, 0),
        (["telemetry", "-w", "2"], 2, 0),
        (["telemetry", "--watch", "5", "--replay"], 5, 600),
        (["telemetry", "--replay", "20"], None, 20),
    ],
)
def test_parser_telemetry_watch_and_replay(argv, watch, replay) -> None:
    args = build_parser().parse_args(argv)
    assert args.watch == watch
    assert args.replay == replay


def test_parser_telemetry_once_removed() -> None:
    """Мёртвый --once удалён: его никто не читал, режим задаёт отсутствие -w."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["telemetry", "--once"])


# --- doctor / version ---


def test_doctor_missing_required_fails(monkeypatch, capsys) -> None:
    import arctuner.igcl.loader as loader_mod

    class FakeLoader:
        def healthcheck(self) -> dict[str, object]:
            return {"path": "C:/Windows/System32/igcl.dll", "missing_required": ["ctlFoo"]}

    monkeypatch.setattr(loader_mod, "IgclLoader", FakeLoader)
    rc = cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 1
    assert "[FAIL]" in out
    assert "ctlFoo" in out
    assert "в порядке" not in out  # успешный вердикт при нехватке функций запрещён


def test_version_falls_back_to_package_version(monkeypatch) -> None:
    def not_installed(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    assert _version() == __version__


# --- журнал CLI: reset/fans ---


class _FakeFanApi:
    """Мини-API IGCL для reset/fans: считает вызовы, ничего не пишет на карту."""

    def __init__(self) -> None:
        self.fan = object()
        self.fixed: list[int] = []
        self.defaults = 0
        self.resets = 0

    def __enter__(self) -> _FakeFanApi:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def enum_fans(self, handle: object) -> list[object]:
        return [self.fan]

    def get_fan_properties(self, fan: object) -> SimpleNamespace:
        return SimpleNamespace(supportedModes=2, canControl=True, maxRPM=3000)

    def get_fan_speed(self, fan: object) -> int:
        return 1200

    def set_fan_fixed_speed(self, fan: object, rpm: int) -> None:
        self.fixed.append(rpm)

    def set_fan_default(self, fan: object) -> None:
        self.defaults += 1

    def reset_to_default(self, handle: object) -> None:
        self.resets += 1


def _fake_igcl(monkeypatch, api: _FakeFanApi) -> None:
    monkeypatch.setattr("arctuner.igcl.api.IgclApi", lambda: api)
    monkeypatch.setattr(
        "arctuner.telemetry.find_arc_adapter",
        lambda _api: (1, SimpleNamespace(name="Intel Arc B580")),
    )


def test_reset_oc_writes_marker_and_invalidates_targets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    Logbook(default_logbook_path()).record("powerLimit", 100.0, 120.0)
    api = _FakeFanApi()
    _fake_igcl(monkeypatch, api)

    args = argparse.Namespace(oc=True, fans=False, yes=True)
    assert cmd_reset(args) == 0
    log = Logbook(default_logbook_path())
    assert log.read_all()[-1].field == RESET_OC_FIELD
    assert log.rollback_targets() == {}
    assert api.resets == 1


def test_reset_fans_writes_marker(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    Logbook(default_logbook_path()).record("fan[0]", 1200.0, 1800.0)
    api = _FakeFanApi()
    _fake_igcl(monkeypatch, api)

    args = argparse.Namespace(oc=False, fans=True, yes=True)
    assert cmd_reset(args) == 0
    log = Logbook(default_logbook_path())
    assert log.read_all()[-1].field == RESET_FANS_FIELD
    assert api.defaults == 1


def test_fans_set_journals_old_and_new_rpm(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FakeFanApi()
    _fake_igcl(monkeypatch, api)

    args = argparse.Namespace(set=1800, yes=True)
    assert cmd_fans(args) == 0
    assert api.fixed == [1800]
    entry = Logbook(default_logbook_path()).read_all()[-1]
    assert entry.field == "fan[0]"
    assert entry.old == 1200.0
    assert entry.new == 1800.0
    assert entry.device == "Intel Arc B580"


def test_fans_set_blocks_unknown_max_rpm(monkeypatch, tmp_path, capsys) -> None:
    """maxRPM=0: CLI не пишет вслепую и не журналирует фиктивную запись."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))

    class NoMaxApi(_FakeFanApi):
        def get_fan_properties(self, fan: object) -> SimpleNamespace:
            return SimpleNamespace(supportedModes=2, canControl=True, maxRPM=0)

    api = NoMaxApi()
    _fake_igcl(monkeypatch, api)
    args = argparse.Namespace(set=1800, yes=True)
    assert cmd_fans(args) == 0
    assert api.fixed == []
    assert "maxRPM неизвестен" in capsys.readouterr().out
    assert Logbook(default_logbook_path()).read_all() == []


def test_fans_set_blocks_below_min_rpm(monkeypatch, tmp_path, capsys) -> None:
    """Фиксация ниже MIN_FAN_RPM блокируется до подтверждения и записи."""
    from arctuner.context import MIN_FAN_RPM

    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FakeFanApi()
    _fake_igcl(monkeypatch, api)
    args = argparse.Namespace(set=MIN_FAN_RPM - 1, yes=True)
    assert cmd_fans(args) == 1
    assert api.fixed == []
    out = capsys.readouterr().out
    assert "[BLOCK]" in out and str(MIN_FAN_RPM) in out
    assert Logbook(default_logbook_path()).read_all() == []


# --- cmd_set: откат частичной записи ---


class _PartialApi(_FakeFanApi):
    """OC-API: powerLimit пишется, freq_offset всегда падает (сбой 2-го пункта)."""

    def __init__(self) -> None:
        super().__init__()
        self.set_values: list[float] = []

    def set_power_limit(self, handle: object, value: float) -> None:
        self.set_values.append(value)

    def set_gpu_freq_offset(self, handle: object, value: float) -> None:
        from arctuner.igcl.api import IgclError

        raise IgclError(0x44000001, "ctlOverclockFrequencyOffsetSetV2")

    # --- всё ниже — read-only заглушки, чтобы cmd_set дошёл до записи ---

    def __enter__(self) -> _PartialApi:
        return self

    def enumerate_devices(self) -> list[object]:
        return [object()]

    def get_device_properties(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(name="Intel Arc B580")

    def read_oc_properties(self, handle: object) -> dict:
        from arctuner.igcl.api import OcControlInfo

        info = OcControlInfo(
            supported=True, relative=False, units=0, min=50.0, max=120.0, step=1.0, default=100.0
        )
        freq = OcControlInfo(
            supported=True, relative=True, units=0, min=-300.0, max=1000.0, step=1.0, default=0.0
        )
        return {"powerLimit": info, "gpuFrequencyOffset": freq}

    def read_v2_value(self, handle: object, name: str) -> float | None:
        return None


def test_cmd_set_reverts_applied_on_partial_failure(monkeypatch, tmp_path, capsys) -> None:
    """Сбой на втором пункте: первый возвращается к «было», код 1, журнал честен."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _PartialApi()
    _fake_igcl(monkeypatch, api)

    # _read_current идёт через V2 Get-функции — их нет, old=None, но откат
    # в _revert_partial использует «новое→старое» из applied — подставим old
    # через tuner._read_current, чтобы откат имел значение.
    from arctuner import tuner

    monkeypatch.setattr(tuner, "_read_current", lambda _api, _h, field: 100.0)

    args = argparse.Namespace(
        power_limit=115.0,
        freq_offset=100.0,
        voltage_offset=None,
        temp_limit=None,
        vram_speed=None,
        yes=True,
        force=False,
    )
    assert cmd_set(args) == 1
    out = capsys.readouterr().out
    # Первая запись прошла (115), второй вызов упал → возврат первой к 100.
    assert api.set_values == [115.0, 100.0]
    assert "возвращаю" in out
    # Журнал: применение + откат — обе записи с полем powerLimit.
    fields = [e.field for e in Logbook(default_logbook_path()).read_all()]
    assert fields == ["powerLimit", "powerLimit"]


# --- main(): последний рубеж ---


def test_main_catch_all_returns_one(monkeypatch, capsys) -> None:
    """Необработанное исключение команды → код 1 и [UNEXPECTED], не traceback."""

    def boom(_args: argparse.Namespace) -> int:
        raise RuntimeError("тестовый сбой")

    monkeypatch.setattr("arctuner.cli.cmd_doctor", boom)
    assert main(["doctor"]) == 1
    assert "[UNEXPECTED]" in capsys.readouterr().out


# --- autofind: паритет с GUI ---


def test_autofind_uses_engine_and_maps_config(autofind_env) -> None:
    rc = _autofind([])
    assert rc == 0
    engine = FakeEngine.last
    assert engine is not None
    assert engine.cfg.mode == "frequency"
    assert engine.cfg.step == 15.0  # default для frequency
    assert engine.cfg.min_step == 3.0  # max(15*0.2, 0.1)
    assert engine.cfg.max_steps == 20
    assert engine.cfg.settle_seconds == 1.0
    assert engine.cfg.temp_ceiling_c == 80.0
    assert engine.cfg.require_load is True
    assert engine.cfg.load_timeout == 3.0
    assert isinstance(engine.watchdog, FakeWatchdog)
    assert engine.watchdog.device == "Intel Arc B580"
    assert "on_step" in engine.run_kwargs
    assert "on_status" in engine.run_kwargs


def test_autofind_undervolt_step_and_min_step(autofind_env) -> None:
    FakeEngine.result = TuningResult(
        mode="undervolt",
        baseline=0.0,
        best_stable=-3.0,
        steps_taken=3,
        stopped_reason="граница",
        card_value=-3.0,
    )
    rc = _autofind(["--mode", "undervolt"])
    assert rc == 0
    engine = FakeEngine.last
    assert engine is not None
    assert engine.cfg.mode == "undervolt"
    assert engine.cfg.step == 1.0  # default для undervolt
    assert engine.cfg.min_step == pytest.approx(0.2)  # max(1*0.2, 0.1)


def test_autofind_custom_step_min_step_and_no_load(autofind_env) -> None:
    rc = _autofind(["--step", "5", "--no-load-check"])
    assert rc == 0
    engine = FakeEngine.last
    assert engine is not None
    assert engine.cfg.step == 5.0
    assert engine.cfg.min_step == pytest.approx(1.0)  # max(5*0.2, 0.1)
    assert engine.cfg.require_load is False


def test_autofind_does_not_write_frequency_or_undervolt(autofind_env, tmp_path) -> None:
    """Итоговой записи result.mode нет: журнал наполняет движок реальными полями."""
    assert _autofind([]) == 0
    fake_log = FakeContext.last.log if FakeContext.last else None
    assert fake_log is not None
    assert fake_log.fields == []  # cmd не писал в журнал сам
    real = Logbook(tmp_path / "logbook.jsonl").read_all()
    assert all(e.field not in ("frequency", "undervolt") for e in real)
    assert real == []  # фейковый движок карту не трогал — записей быть не должно


def test_autofind_warns_when_card_value_unknown(autofind_env, capsys) -> None:
    FakeEngine.result = TuningResult(
        mode="frequency",
        baseline=0.0,
        best_stable=45.0,
        steps_taken=1,
        stopped_reason="watchdog: TDR",
        card_value=None,
    )
    assert _autofind([]) == 0
    out = capsys.readouterr().out
    assert "НЕ подтверждено" in out
    assert "status" in out


def test_autofind_prints_default_journal(autofind_env, capsys) -> None:
    """В итоге печатается путь журнала по умолчанию (тот же, что у контекста)."""
    assert _autofind([]) == 0
    out = capsys.readouterr().out
    assert str(default_logbook_path()) in out
    assert default_logbook_path().name == "logbook.jsonl"
