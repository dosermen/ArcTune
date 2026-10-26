"""Тесты кодов выхода CLI: контракт main() и команд — без железа, только моки.

Контракт:
  0   — успех;
  1   — отказ/ошибка (нет IGCL, нет Arc, IgclError, SafetyLimitError, отмена, EOF);
  2   — ошибка использования (argparse; set/reset без параметров);
  130 — KeyboardInterrupt (Ctrl+C).
SystemExit от argparse проходит насквозь: --help → 0, неверные аргументы → 2.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from arctuner.cli import (
    build_parser,
    cmd_fans,
    cmd_reset,
    cmd_set,
    cmd_status,
    cmd_telemetry,
    main,
)
from arctuner.igcl.api import OcControlInfo, TelemetrySnapshot
from arctuner.logbook import RESET_OC_FIELD, Logbook, default_logbook_path


def _snap() -> TelemetrySnapshot:
    """Снимок телеметрии: все поля дефолтные, кроме счётчиков энергии."""
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


def _dev(device_id: int = 0xE20B) -> SimpleNamespace:
    """DeviceInfo-подобная заглушка для find_arc_adapter."""
    return SimpleNamespace(
        name="Intel Arc B580",
        device_id=device_id,
        frequency_mhz=2600,
        num_xe_cores=20,
    )


def _power_info() -> OcControlInfo:
    """Диапазон powerLimit как у B580: 50..120 % (120 — аппаратный потолок)."""
    return OcControlInfo(
        supported=True, relative=False, units=0, min=50.0, max=120.0, step=1.0, default=100.0
    )


class _NoLoader:
    """Лоадер-заглушка: V2 Get-функций нет → текущие значения не читаются."""

    def has(self, name: str) -> bool:
        return False


class _FakeApi:
    """Мини-API IGCL: контекст, телеметрия, OC-свойства, запись powerLimit."""

    def __init__(self) -> None:
        self.loader = _NoLoader()
        self.written_power: list[float] = []
        self.resets = 0

    def __enter__(self) -> _FakeApi:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def read_power_telemetry(self, handle: object) -> TelemetrySnapshot:
        return _snap()

    def read_oc_properties(self, handle: object) -> dict:
        return {"supported": True, "powerLimit": _power_info()}

    def get_device_properties(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(name="Intel Arc B580", device_id=0xE20B)

    def set_power_limit(self, handle: object, value: float) -> None:
        self.written_power.append(value)

    def reset_to_default(self, handle: object) -> None:
        self.resets += 1


class _BrokenWriteApi(_FakeApi):
    """Запись powerLimit падает как ошибка драйвера."""

    def set_power_limit(self, handle: object, value: float) -> None:
        from arctuner.igcl.api import IgclError

        raise IgclError(0x44000001, "ctlOverclockPowerLimitSetV2")


class _FanApi(_FakeApi):
    """Вентиляторная часть API для cmd_fans."""

    def __init__(self) -> None:
        super().__init__()
        self.fan = object()
        self.fixed: list[int] = []

    def enum_fans(self, handle: object) -> list[object]:
        return [self.fan]

    def get_fan_properties(self, fan: object) -> SimpleNamespace:
        return SimpleNamespace(supportedModes=2, canControl=True, maxRPM=3000)

    def get_fan_speed(self, fan: object) -> int:
        return 1200

    def set_fan_fixed_speed(self, fan: object, rpm: int) -> None:
        self.fixed.append(rpm)


def _fake_igcl(monkeypatch, api: _FakeApi, found: bool = True) -> None:
    """Подменяет IgclApi и поиск Arc-адаптера (паттерн из test_cli.py)."""
    monkeypatch.setattr("arctuner.igcl.api.IgclApi", lambda: api)
    if found:
        monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: (1, _dev()))
    else:
        monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: None)


# --- main(): разбор аргументов и последний рубеж ---


def test_main_help_raises_systemexit_zero(capsys) -> None:
    """--help штатно завершается через SystemExit(0), не через return."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    capsys.readouterr()  # забираем вывод help из лога


def test_main_bad_arguments_raises_systemexit_two(capsys) -> None:
    """Неизвестный аргумент — usage от argparse и SystemExit(2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["telemetry", "--bogus-flag"])
    assert excinfo.value.code == 2
    capsys.readouterr()


def test_main_doctor_ok_returns_zero(monkeypatch, capsys) -> None:
    """Успешный doctor через main() → 0."""
    import arctuner.igcl.loader as loader_mod

    class OkLoader:
        def healthcheck(self) -> dict[str, object]:
            return {"path": "C:/Windows/System32/igcl.dll", "missing_required": []}

    monkeypatch.setattr(loader_mod, "IgclLoader", OkLoader)
    assert main(["doctor"]) == 0
    assert "[OK]" in capsys.readouterr().out


def test_main_doctor_not_found_returns_one(monkeypatch, capsys) -> None:
    """IGCL не найден → doctor → 1 через main()."""
    import arctuner.igcl.loader as loader_mod
    from arctuner.igcl.loader import IgclNotFoundError

    class NoDll:
        def __init__(self) -> None:
            raise IgclNotFoundError("IGCL не найден")

    monkeypatch.setattr(loader_mod, "IgclLoader", NoDll)
    assert main(["doctor"]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys) -> None:
    """KeyboardInterrupt → 130, «Прервано пользователем», не traceback."""

    def boom(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("arctuner.cli.cmd_doctor", boom)
    assert main(["doctor"]) == 130
    assert "Прервано" in capsys.readouterr().out


def test_main_reraises_systemexit_from_command(monkeypatch) -> None:
    """SystemExit от команды не «съедается» catch-all'ом — пробрасывается."""

    def halt(_args: argparse.Namespace) -> int:
        raise SystemExit(5)

    monkeypatch.setattr("arctuner.cli.cmd_doctor", halt)
    with pytest.raises(SystemExit) as excinfo:
        main(["doctor"])
    assert excinfo.value.code == 5


# --- telemetry ---


def test_telemetry_success_returns_zero(monkeypatch, capsys) -> None:
    _fake_igcl(monkeypatch, _FakeApi())
    assert cmd_telemetry(build_parser().parse_args(["telemetry"])) == 0
    out = capsys.readouterr().out
    assert "Intel Arc B580" in out
    assert "Телеметрия" in out


def test_telemetry_without_adapter_returns_one(monkeypatch, capsys) -> None:
    _fake_igcl(monkeypatch, _FakeApi(), found=False)
    assert cmd_telemetry(build_parser().parse_args(["telemetry"])) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_telemetry_igcl_error_returns_one(monkeypatch, capsys) -> None:
    """Сбой чтения телеметрии ловится catch-all'ом команды → 1, не traceback."""

    class BrokenApi(_FakeApi):
        def read_power_telemetry(self, handle: object) -> TelemetrySnapshot:
            from arctuner.igcl.api import IgclError

            raise IgclError(0x44000002, "ctlPowerTelemetryGet")

    _fake_igcl(monkeypatch, BrokenApi())
    assert cmd_telemetry(build_parser().parse_args(["telemetry"])) == 1
    assert "[UNEXPECTED]" in capsys.readouterr().out


# --- status ---


def test_status_success_returns_zero(monkeypatch, capsys) -> None:
    _fake_igcl(monkeypatch, _FakeApi())
    assert cmd_status(build_parser().parse_args(["status"])) == 0
    out = capsys.readouterr().out
    assert "powerLimit" in out
    assert "Разгон поддерживается" in out


def test_status_without_adapter_returns_one(monkeypatch, capsys) -> None:
    _fake_igcl(monkeypatch, _FakeApi(), found=False)
    assert cmd_status(build_parser().parse_args(["status"])) == 1
    assert "[FAIL]" in capsys.readouterr().out


# --- set ---


def test_set_without_changes_returns_two(capsys) -> None:
    """set без параметров — ошибка использования (2), IGCL не трогается."""
    assert cmd_set(build_parser().parse_args(["set"])) == 2
    assert "Нечего менять" in capsys.readouterr().out


def test_set_decline_returns_one(monkeypatch, capsys) -> None:
    """Отказ пользователя от записи → 1, карта не тронута."""
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    args = build_parser().parse_args(["set", "--power-limit", "110"])
    assert cmd_set(args) == 1
    assert "Отменено" in capsys.readouterr().out


def test_set_eof_returns_one(monkeypatch, capsys) -> None:
    """Конец ввода вместо ответа — отмена записи, код 1."""

    def eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    args = build_parser().parse_args(["set", "--power-limit", "110"])
    assert cmd_set(args) == 1
    assert "конец ввода" in capsys.readouterr().out


def test_set_without_adapter_returns_one(monkeypatch) -> None:
    _fake_igcl(monkeypatch, _FakeApi(), found=False)
    args = build_parser().parse_args(["set", "--power-limit", "110", "--yes"])
    assert cmd_set(args) == 1


def test_set_safety_limit_returns_one(monkeypatch, tmp_path, capsys) -> None:
    """500% питания — за жёстким потолком B580 (120%) → SafetyLimitError → 1."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    _fake_igcl(monkeypatch, _FakeApi())
    args = build_parser().parse_args(["set", "--power-limit", "500", "--yes"])
    assert cmd_set(args) == 1
    assert "[BLOCK]" in capsys.readouterr().out
    assert Logbook(default_logbook_path()).read_all() == []  # запись не прошла — журнала нет


def test_set_igcl_error_returns_one(monkeypatch, tmp_path, capsys) -> None:
    """Ошибка драйвера при записи → 1 (частичный откат уже покрыт в test_cli)."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    _fake_igcl(monkeypatch, _BrokenWriteApi())
    args = build_parser().parse_args(["set", "--power-limit", "110", "--yes"])
    assert cmd_set(args) == 1
    assert "[BLOCK]" in capsys.readouterr().out


def test_set_success_returns_zero(monkeypatch, tmp_path) -> None:
    """Успешная запись → 0, вызов API и запись журнала на месте."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FakeApi()
    _fake_igcl(monkeypatch, api)
    args = build_parser().parse_args(["set", "--power-limit", "110", "--yes"])
    assert cmd_set(args) == 0
    assert api.written_power == [110.0]
    entry = Logbook(default_logbook_path()).read_all()[-1]
    assert entry.field == "powerLimit"
    assert entry.new == 110.0


# --- reset ---


def test_reset_without_flags_returns_two(capsys) -> None:
    """reset без --oc/--fans — ошибка использования (2), IGCL не трогается."""
    args = argparse.Namespace(oc=False, fans=False, yes=True)
    assert cmd_reset(args) == 2
    assert "Укажите" in capsys.readouterr().out


def test_reset_without_adapter_returns_one(monkeypatch) -> None:
    _fake_igcl(monkeypatch, _FakeApi(), found=False)
    args = argparse.Namespace(oc=True, fans=False, yes=True)
    assert cmd_reset(args) == 1


def test_reset_oc_success_returns_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FakeApi()
    _fake_igcl(monkeypatch, api)
    args = argparse.Namespace(oc=True, fans=False, yes=True)
    assert cmd_reset(args) == 0
    assert api.resets == 1
    assert Logbook(default_logbook_path()).read_all()[-1].field == RESET_OC_FIELD


# --- fans ---


def test_fans_without_adapter_returns_one(monkeypatch) -> None:
    _fake_igcl(monkeypatch, _FanApi(), found=False)
    assert cmd_fans(build_parser().parse_args(["fans"])) == 1


def test_fans_decline_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    args = build_parser().parse_args(["fans", "--set", "1800"])
    assert cmd_fans(args) == 1
    assert "Отменено" in capsys.readouterr().out


def test_fans_set_success_returns_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    api = _FanApi()
    _fake_igcl(monkeypatch, api)
    args = build_parser().parse_args(["fans", "--set", "1800", "--yes"])
    assert cmd_fans(args) == 0
    assert api.fixed == [1800]
    assert Logbook(default_logbook_path()).read_all()[-1].field == "fan[0]"
