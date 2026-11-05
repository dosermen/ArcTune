"""Тесты exit-кодов CLI: 0 — успех, 1 — ошибка, 2 — неверные аргументы, 130 — Ctrl+C.

Каждая команда проходит через main() с фейковым IgclApi/find_arc_adapter/tuner —
реального IGCL.dll здесь нет и быть не может (без hardware-метки).

Реальные коды cli.py:
    main          — 130 при KeyboardInterrupt, 1 при необработанном исключении,
                    SystemExit (argparse --help/--version) перебрасывается;
    cmd_set       — 2 «нечего менять», 1 отмена подтверждения/ошибка записи;
    cmd_reset     — 2 «не указано что сбрасывать»;
    cmd_telemetry — 0 Ctrl+C (штатное завершение watch), 1 ошибка/нет Arc.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import arctuner.cli as cli_mod
from arctuner.autotune import engine as engine_mod
from arctuner.autotune import watchdog as watchdog_mod
from arctuner.cli import main
from arctuner.igcl.api import IgclError, TelemetrySnapshot
from arctuner.logbook import (
    RESET_FANS_FIELD,
    RESET_OC_FIELD,
    JournalWriteError,
    Logbook,
    default_logbook_path,
)
from arctuner.tuner import SafetyLimitError, WriteReport

_DEV = SimpleNamespace(
    name="Intel Arc B580",
    vendor_id=0x8086,
    device_id=0xE20B,
    subsystem_vendor_id=0x8086,
    subsystem_id=0x1025,
    rev_id=0,
    frequency_mhz=2670,
    num_xe_cores=20,
)


def _snap() -> TelemetrySnapshot:
    """Минимальный снимок телеметрии для фейкового read_power_telemetry."""
    return TelemetrySnapshot(
        gpu_voltage_v=1.0,
        gpu_freq_mhz=2500.0,
        gpu_temp_c=55.0,
        gpu_power_percent=70.0,
        vram_voltage_v=1.35,
        vram_eff_freq_mbps=19.75,
        vram_temp_c=48.0,
        power_limited=False,
        temperature_limited=False,
        current_limited=False,
        voltage_limited=False,
        utilization_limited=False,
        activity_counter=42,
        gpu_energy_counter_j=1000.0,
        card_energy_counter_j=5000.0,
        vram_energy_counter_j=100.0,
    )


class _FakeApi:
    """IgclApi-дублёр: контекстный менеджер + read-only заглушки без DLL.

    write-методы не определены намеренно: тесты подмешивают их через
    setattr экземпляра или monkeypatch tuner-функций.
    """

    def __init__(self) -> None:
        self.power = _snap()

    def __enter__(self) -> _FakeApi:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def read_power_telemetry(self, handle: object) -> TelemetrySnapshot:
        return self.power

    def read_oc_properties(self, handle: object) -> dict:
        return {"supported": False}

    def read_vf_curve(self, handle: object) -> list:
        return [SimpleNamespace(voltage_mv=700, freq_mhz=2800)]

    def get_pci_state(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(
            gen=5, width=16, address="0000:03:00.0", rebar_enabled=True, rebar_supported=True
        )

    def get_firmware_properties(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(name="i915", version="1.0")

    def read_raw_device_properties(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(
            driver_version=(32 << 48) | (1 << 32) | (101 << 16) | 9150,
            firmware_version=SimpleNamespace(major_version=3, minor_version=7),
        )

    def get_ecc_state(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(current="disabled", pending="disabled")

    def enum_fans(self, handle: object) -> list:
        return []


@pytest.fixture()
def cli_env(monkeypatch, tmp_path: Path) -> _FakeApi:
    """Изолирует журнал (ARCTUNE_LOG) и подменяет IGCL-слой фейками."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FakeApi()
    monkeypatch.setattr("arctuner.igcl.api.IgclApi", lambda: api)
    monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: (1, _DEV))
    monkeypatch.setattr("arctuner.tuner.current_values", lambda _api, _handle: {})
    return api


# --- успех (0) ---


def test_read_only_commands_return_zero(cli_env: _FakeApi) -> None:
    """telemetry/status/info/vf/log на фейках завершаются с кодом 0."""
    assert main(["telemetry"]) == 0
    assert main(["status"]) == 0
    assert main(["info"]) == 0
    assert main(["info", "--detailed"]) == 0
    assert main(["vf"]) == 0
    assert main(["log"]) == 0  # пустой журнал — не ошибка, просто нечего показывать


def test_info_detailed_prints_ids_and_versions(cli_env: _FakeApi, capsys) -> None:
    assert main(["info", "--detailed"]) == 0

    output = capsys.readouterr().out
    assert "0X8086" in output
    assert "0XE20B" in output
    assert "0X00" in output
    assert "32.1.101.9150" in output
    assert "3.7" in output


def test_telemetry_csv_writes_file(cli_env: _FakeApi, tmp_path: Path, capsys) -> None:
    """telemetry --csv: общий export_telemetry пишет header + строку снимка."""
    out = tmp_path / "snap.csv"
    assert main(["telemetry", "--csv", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # header + один снимок
    assert lines[0].startswith("timestamp,gpu_freq_mhz")
    assert "2500" in lines[1]  # gpu_freq_mhz из _snap
    assert "-> CSV" in capsys.readouterr().out


def test_doctor_success_returns_zero(monkeypatch, capsys) -> None:
    import arctuner.igcl.loader as loader_mod

    class FakeLoader:
        def healthcheck(self) -> dict:
            return {"path": "C:/Windows/System32/igcl.dll", "missing_required": []}

    monkeypatch.setattr(loader_mod, "IgclLoader", FakeLoader)
    assert main(["doctor"]) == 0
    assert "в порядке" in capsys.readouterr().out


def test_telemetry_keyboard_interrupt_returns_zero(cli_env: _FakeApi, capsys) -> None:
    """Ctrl+C в telemetry — штатное завершение watch, а не ошибка (код 0)."""

    def interrupt(handle: object) -> TelemetrySnapshot:
        raise KeyboardInterrupt

    cli_env.read_power_telemetry = interrupt  # type: ignore[method-assign]
    assert main(["telemetry"]) == 0
    assert "Остановлено" in capsys.readouterr().out


def test_set_success_returns_zero(cli_env: _FakeApi, monkeypatch) -> None:
    def ok(_api: object, _handle: object, value: float) -> WriteReport:
        return WriteReport(field="powerLimit", old=100.0, new=value, note="")

    monkeypatch.setattr("arctuner.tuner.set_power_limit", ok)
    assert main(["set", "--power-limit", "115", "--yes"]) == 0
    entries = Logbook(default_logbook_path()).read_all()
    assert [e.field for e in entries] == ["powerLimit"]
    assert entries[0].old == 100.0
    assert entries[0].new == 115.0


def test_fans_no_fans_returns_zero(cli_env: _FakeApi) -> None:
    """Нет вентиляторов (enum пуст) — не ошибка, читать нечего."""
    assert main(["fans"]) == 0


def test_log_rollback_empty_returns_zero(cli_env: _FakeApi) -> None:
    """Пустой журнал: rollback — no-op с кодом 0 («нечего откатывать»)."""
    assert main(["log", "--rollback", "--yes"]) == 0


def test_log_rollback_success_returns_zero(cli_env: _FakeApi, monkeypatch) -> None:
    Logbook(default_logbook_path()).record("powerLimit", 100.0, 110.0)
    calls: list[float] = []

    def restore(_api: object, _handle: object, value: float) -> WriteReport:
        calls.append(value)
        return WriteReport(field="powerLimit", old=110.0, new=value, note="")

    # Подменяем саму карту: _build_rollback_map не перезаполняет непустой dict.
    monkeypatch.setattr(cli_mod, "_ROLLBACK_MAP", {"powerLimit": restore})
    assert main(["log", "--rollback", "--yes"]) == 0
    assert calls == [100.0]
    assert Logbook(default_logbook_path()).read_all()[0].restored is True


def test_log_rollback_blocked_target_does_not_fail_command(
    cli_env: _FakeApi, monkeypatch, capsys
) -> None:
    """SafetyLimitError по одному пункту (диапазоны IGCL сменились) → код 0."""
    log = Logbook(default_logbook_path())
    log.record("powerLimit", 100.0, 110.0)
    log.record("gpuFrequencyOffset", 0.0, 50.0)

    def blocked(_api: object, _handle: object, _value: float) -> WriteReport:
        raise SafetyLimitError("диапазон сменён драйвером")

    ok_calls: list[float] = []

    def ok(_api: object, _handle: object, value: float) -> WriteReport:
        ok_calls.append(value)
        return WriteReport(field="gpuFrequencyOffset", old=0.0, new=value, note="")

    monkeypatch.setattr(cli_mod, "_ROLLBACK_MAP", {"powerLimit": blocked, "gpuFrequencyOffset": ok})
    assert main(["log", "--rollback", "--yes"]) == 0
    assert ok_calls == [0.0]
    out = capsys.readouterr().out
    assert "[BLOCK]" in out and "Откат завершён" in out
    # Восстановлено только успешное поле.
    flags = {e.field: e.restored for e in log.read_all()}
    assert flags == {"powerLimit": False, "gpuFrequencyOffset": True}


# --- неверные аргументы (2) ---


def test_set_without_params_returns_two() -> None:
    assert main(["set"]) == 2


def test_reset_without_flags_returns_two() -> None:
    assert main(["reset"]) == 2


# --- отказ пользователя / EOF (1) ---


def test_set_declined_confirmation_returns_one(cli_env: _FakeApi, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["set", "--power-limit", "115"]) == 1
    assert Logbook(default_logbook_path()).read_all() == []  # ничего не записано


def test_set_eof_returns_one(cli_env: _FakeApi, monkeypatch) -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main(["set", "--power-limit", "115"]) == 1


def test_log_rollback_declined_returns_one(cli_env: _FakeApi, monkeypatch) -> None:
    Logbook(default_logbook_path()).record("powerLimit", 100.0, 110.0)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["log", "--rollback"]) == 1


def test_autofind_declined_returns_one(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["autofind"]) == 1


def test_autofind_eof_returns_one(monkeypatch) -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert main(["autofind"]) == 1


# --- ошибка пользователя/драйвера (1) ---


@pytest.mark.parametrize(
    "argv",
    [
        ["telemetry"],
        ["status"],
        ["info"],
        ["vf"],
        ["fans"],
        ["reset", "--oc", "--yes"],
        ["set", "--power-limit", "115", "--yes"],
    ],
)
def test_no_arc_adapter_returns_one(cli_env: _FakeApi, monkeypatch, argv: list[str]) -> None:
    """Дискретная Arc не найдена — все команды, трогающие карту, выходят с 1."""
    monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: None)
    assert main(argv) == 1


def test_set_safety_limit_returns_one(cli_env: _FakeApi, monkeypatch, capsys) -> None:
    def blocked(_api: object, _handle: object, _value: float) -> WriteReport:
        raise SafetyLimitError("115% выше предела B580")

    monkeypatch.setattr("arctuner.tuner.set_power_limit", blocked)
    assert main(["set", "--power-limit", "115", "--yes"]) == 1
    assert "[BLOCK]" in capsys.readouterr().out


def test_set_igcl_error_returns_one(cli_env: _FakeApi, monkeypatch, capsys) -> None:
    """Сбой записи драйвера (IgclError) внутри цикла → [BLOCK] и код 1."""

    def broken(_api: object, _handle: object, _value: float) -> WriteReport:
        raise IgclError(0x44000001, "ctlOverclockPowerLimitSet")

    monkeypatch.setattr("arctuner.tuner.set_power_limit", broken)
    assert main(["set", "--power-limit", "115", "--yes"]) == 1
    assert "[BLOCK]" in capsys.readouterr().out


def test_doctor_not_found_returns_one(monkeypatch, capsys) -> None:
    import arctuner.igcl.loader as loader_mod

    def no_dll() -> object:
        raise loader_mod.IgclNotFoundError("IGCL.dll не найден")

    monkeypatch.setattr(loader_mod, "IgclLoader", no_dll)
    assert main(["doctor"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_telemetry_unexpected_error_returns_one(cli_env: _FakeApi, capsys) -> None:
    def broken(handle: object) -> TelemetrySnapshot:
        raise RuntimeError("драйвер упал")

    cli_env.read_power_telemetry = broken  # type: ignore[method-assign]
    assert main(["telemetry"]) == 1
    assert "[UNEXPECTED]" in capsys.readouterr().out


def test_telemetry_igcl_error_returns_one(cli_env: _FakeApi, capsys) -> None:
    """IgclError при чтении телеметрии тоже уходит в catch-all команды → 1."""

    def broken(handle: object) -> TelemetrySnapshot:
        raise IgclError(0x44000002, "ctlPowerTelemetryGet")

    cli_env.read_power_telemetry = broken  # type: ignore[method-assign]
    assert main(["telemetry"]) == 1
    assert "[UNEXPECTED]" in capsys.readouterr().out


def test_vf_empty_curve_returns_one(cli_env: _FakeApi, capsys) -> None:
    """Кривая не читается — честный [FAIL] с кодом 1, не пустой успех."""
    cli_env.read_vf_curve = lambda _handle: []  # type: ignore[method-assign]
    assert main(["vf"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_vf_igcl_error_returns_one(cli_env: _FakeApi, capsys) -> None:
    def broken(_handle: object) -> list:
        raise IgclError(0x40000006, "ctlOverclockVfCurveGet")

    cli_env.read_vf_curve = broken  # type: ignore[method-assign]
    assert main(["vf"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_fans_declined_confirmation_returns_one(cli_env: _FakeApi, monkeypatch) -> None:
    """Отказ пользователя от фиксации RPM → 1, карта не тронута."""
    fan = object()
    fixed: list[int] = []
    cli_env.enum_fans = lambda _handle: [fan]  # type: ignore[method-assign]
    cli_env.get_fan_properties = lambda _f: SimpleNamespace(  # type: ignore[method-assign]
        supportedModes=2, canControl=True, maxRPM=3000
    )
    cli_env.get_fan_speed = lambda _f: 1200  # type: ignore[method-assign]
    cli_env.set_fan_fixed_speed = lambda _f, rpm: fixed.append(rpm)  # type: ignore[method-assign]
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["fans", "--set", "1800"]) == 1
    assert fixed == []  # ни одной записи на карту


def test_fans_set_success_returns_zero(cli_env: _FakeApi) -> None:
    """Фиксация RPM → 0, запись API и журнал fan[0] на месте."""
    fan = object()
    fixed: list[int] = []
    cli_env.enum_fans = lambda _handle: [fan]  # type: ignore[method-assign]
    cli_env.get_fan_properties = lambda _f: SimpleNamespace(  # type: ignore[method-assign]
        supportedModes=2, canControl=True, maxRPM=3000
    )
    cli_env.get_fan_speed = lambda _f: 1200  # type: ignore[method-assign]
    cli_env.set_fan_fixed_speed = lambda _f, rpm: fixed.append(rpm)  # type: ignore[method-assign]
    assert main(["fans", "--set", "1800", "--yes"]) == 0
    assert fixed == [1800]
    entry = Logbook(default_logbook_path()).read_all()[-1]
    assert entry.field == "fan[0]"
    assert entry.new == 1800.0


def test_fans_journal_failure_returns_one_and_reverts_fan(
    cli_env: _FakeApi, monkeypatch, capsys
) -> None:
    """Журнал не записался после фиксации RPM: вентилятор возвращён в авто, код 1."""
    fan = object()
    cli_env.enum_fans = lambda _handle: [fan]  # type: ignore[method-assign]
    cli_env.get_fan_properties = lambda _f: SimpleNamespace(  # type: ignore[method-assign]
        supportedModes=2, canControl=True, maxRPM=3000
    )
    cli_env.get_fan_speed = lambda _f: 1200  # type: ignore[method-assign]
    cli_env.set_fan_fixed_speed = lambda _f, _rpm: None  # type: ignore[method-assign]
    reverted: list[object] = []
    cli_env.set_fan_default = lambda f: reverted.append(f)  # type: ignore[method-assign]

    class FailingLogbook:
        def __init__(self, path: Path) -> None:
            self.path = path

        def record(self, *_args: object, **_kwargs: object) -> None:
            raise JournalWriteError("нет места в журнале")

    monkeypatch.setattr("arctuner.logbook.Logbook", FailingLogbook)
    assert main(["fans", "--set", "1800", "--yes"]) == 1
    assert reverted == [fan]  # карта возвращена в авто до падения
    assert "журнал не записан" in capsys.readouterr().out


def test_fans_set_rolls_back_earlier_fans_on_later_failure(cli_env: _FakeApi, capsys) -> None:
    """Сбой установки на втором вентиляторе возвращает оба затронутых в авто."""
    fans = [object(), object()]
    fixed: list[tuple[object, int]] = []
    defaults: list[object] = []
    cli_env.enum_fans = lambda _handle: fans  # type: ignore[method-assign]
    cli_env.get_fan_properties = lambda _fan: SimpleNamespace(  # type: ignore[method-assign]
        supportedModes=2, canControl=True, maxRPM=3000
    )
    cli_env.get_fan_speed = lambda _fan: 1200  # type: ignore[method-assign]

    def set_fixed(fan: object, rpm: int) -> None:
        fixed.append((fan, rpm))
        if fan is fans[1]:
            raise IgclError(0x44000001, "ctlFanSetFixedSpeedMode")

    cli_env.set_fan_fixed_speed = set_fixed  # type: ignore[method-assign]
    cli_env.set_fan_default = lambda fan: defaults.append(fan)  # type: ignore[method-assign]

    assert main(["fans", "--set", "1800", "--yes"]) == 1
    assert fixed == [(fans[0], 1800), (fans[1], 1800)]
    assert defaults == [fans[1], fans[0]]
    fields = [entry.field for entry in Logbook(default_logbook_path()).read_all()]
    assert fields == ["fan[0]", RESET_FANS_FIELD]
    assert "все затронутые вентиляторы возвращены в авто" in capsys.readouterr().out


def test_reset_fans_igcl_error_returns_one(cli_env: _FakeApi, capsys) -> None:
    fan = object()
    cli_env.enum_fans = lambda _handle: [fan]  # type: ignore[method-assign]

    def broken(_f: object) -> None:
        raise IgclError(0x44000001, "ctlFanDefaultSet")

    cli_env.set_fan_default = broken  # type: ignore[method-assign]
    assert main(["reset", "--fans", "--yes"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_reset_oc_success_returns_zero(cli_env: _FakeApi) -> None:
    """reset --oc → 0: карта в дефолте, маркер reset_oc записан в журнал."""
    cli_env.reset_to_default = lambda _handle: None  # type: ignore[method-assign]
    assert main(["reset", "--oc", "--yes"]) == 0
    assert Logbook(default_logbook_path()).read_all()[-1].field == RESET_OC_FIELD


def test_reset_oc_journal_failure_returns_one(cli_env: _FakeApi, monkeypatch, capsys) -> None:
    """OC сброшены, но маркер в журнал не записан — код 1, с честным сообщением."""
    cli_env.reset_to_default = lambda _handle: None  # type: ignore[method-assign]

    def boom(self: object, *_args: object, **_kwargs: object) -> None:
        raise OSError("нет места")

    monkeypatch.setattr("arctuner.logbook.Logbook.record", boom)
    assert main(["reset", "--oc", "--yes"]) == 1
    assert "журнал не записан" in capsys.readouterr().out


# --- autofind: коды ошибок движка ---


class _FakeCtx:
    """GpuContext-дублёр: open/close без IGCL, info = None (нет устройства)."""

    info = None

    def __enter__(self) -> _FakeCtx:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


@pytest.fixture()
def autofind_env(monkeypatch) -> None:
    """Подменяет контекст/движок/watchdog autofind (без железа и Qt)."""
    monkeypatch.setenv("ARCTUNE_LOG", "unused-logbook.jsonl")
    monkeypatch.setattr("arctuner.context.GpuContext", _FakeCtx)
    monkeypatch.setattr(watchdog_mod, "Watchdog", _FakeWatchdog)


class _FakeWatchdog:
    def __init__(self, device: str = "") -> None:
        self.device = device


def _engine_raising(exc: BaseException) -> type:
    class _FailingEngine:
        def __init__(self, ctx: object, cfg: object, *, watchdog: object = None) -> None:
            pass

        def run(self, **_kwargs: object) -> object:
            raise exc

    return _FailingEngine


def test_autofind_abort_returns_one(autofind_env, monkeypatch, capsys) -> None:
    abort = _engine_raising(engine_mod.AutotuneAbortError("нет нагрузки"))
    monkeypatch.setattr(engine_mod, "TuningEngine", abort)
    assert main(["autofind", "--yes"]) == 1
    assert "[ABORT]" in capsys.readouterr().out


def test_autofind_safety_limit_returns_one(autofind_env, monkeypatch, capsys) -> None:
    monkeypatch.setattr(engine_mod, "TuningEngine", _engine_raising(SafetyLimitError("предел")))
    assert main(["autofind", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "выполнен откат" in out


# --- main(): последний рубеж ---


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys) -> None:
    def kb(_args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("arctuner.cli.cmd_doctor", kb)
    assert main(["doctor"]) == 130
    assert "Прервано пользователем" in capsys.readouterr().out


def test_main_help_raises_systemexit_zero() -> None:
    """argparse --help штатно завершается SystemExit(0) — main его не гасит."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_main_bad_arguments_raises_systemexit_two() -> None:
    """Неизвестный аргумент — usage от argparse и SystemExit(2), не return."""
    with pytest.raises(SystemExit) as exc:
        main(["telemetry", "--bogus-flag"])
    assert exc.value.code == 2


def test_main_reraises_systemexit_from_command(monkeypatch) -> None:
    """SystemExit от команды не «съедается» catch-all'ом — пробрасывается."""

    def halt(_args: object) -> int:
        raise SystemExit(5)

    monkeypatch.setattr("arctuner.cli.cmd_doctor", halt)
    with pytest.raises(SystemExit) as exc:
        main(["doctor"])
    assert exc.value.code == 5
