"""Тесты State Doctor: сверка состояния с журналом и версия драйвера (read-only)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arctuner.cli import main
from arctuner.igcl.api import OcControlInfo
from arctuner.state_doctor import (
    FIELD_UNITS,
    StateFinding,
    check_driver_version,
    compare_state,
    format_value,
)

# --- compare_state ---


def test_compare_state_match_returns_ok() -> None:
    findings = compare_state({"powerLimit": 100.0}, {"powerLimit": 100.0})
    assert [f.status for f in findings] == ["ok"]
    assert findings[0].message.startswith("powerLimit: 100 %")


def test_compare_state_drift_reports_expected_and_actual() -> None:
    findings = compare_state({"gpuFrequencyOffset": 0.0}, {"gpuFrequencyOffset": 160.0})
    f = findings[0]
    assert f.status == "drift"
    assert f.expected == 160.0 and f.actual == 0.0
    assert "+160 МГц" in f.message
    assert "+0 МГц" in f.message
    assert "сброшено драйвером?" in f.message


def test_compare_state_tolerates_half_step() -> None:
    """Драйвер округляет до step: отклонение в пределах step/2 — не дрейф."""
    findings = compare_state({"powerLimit": 100.5}, {"powerLimit": 100.0}, {"powerLimit": 1.0})
    assert [f.status for f in findings] == ["ok"]


def test_compare_state_beyond_tolerance_is_drift() -> None:
    findings = compare_state({"powerLimit": 102.0}, {"powerLimit": 100.0}, {"powerLimit": 0.5})
    assert findings[0].status == "drift"
    assert "±0.5 %" in findings[0].message


def test_compare_state_no_log_entry() -> None:
    findings = compare_state({"temperatureLimit": 80.0}, {})
    assert findings[0].status == "no_log"
    assert findings[0].expected is None


def test_compare_state_unreadable_actual() -> None:
    findings = compare_state({"powerLimit": None}, {"powerLimit": 100.0})
    assert findings[0].status == "unknown"


def test_compare_state_ignores_fields_outside_actual() -> None:
    """Поля журнала вне current_values() (вентиляторы, маркеры) не сверяются."""
    findings = compare_state({"powerLimit": 100.0}, {"powerLimit": 100.0, "fan[0]": 1200.0})
    assert len(findings) == 1


def test_compare_state_preserves_actual_order() -> None:
    actual = {"vramMemSpeedLimit": 19.5, "powerLimit": 100.0}
    findings = compare_state(actual, {"vramMemSpeedLimit": 19.5, "powerLimit": 100.0})
    assert [f.field for f in findings] == ["vramMemSpeedLimit", "powerLimit"]


# --- format_value / единицы ---


def test_format_value_signed_fields() -> None:
    assert format_value("gpuFrequencyOffset", 160) == "+160 МГц"
    assert format_value("gpuVoltageOffset", -50) == "-50 %"
    assert format_value("powerLimit", 120) == "120 %"
    assert format_value("temperatureLimit", None) == "—"


def test_field_units_cover_all_current_getters() -> None:
    from arctuner.tuner import _CURRENT_GETTERS

    assert set(FIELD_UNITS) == set(_CURRENT_GETTERS)


# --- check_driver_version ---


def test_driver_version_first_run_saves(tmp_path: Path) -> None:
    path = tmp_path / "driver_version"
    status, previous = check_driver_version("32.0.101.9150", path)
    assert (status, previous) == ("saved", None)
    assert path.read_text(encoding="utf-8").strip() == "32.0.101.9150"


def test_driver_version_same_does_not_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "driver_version"
    check_driver_version("32.0.101.9150", path)
    mtime = path.stat().st_mtime_ns
    status, previous = check_driver_version("32.0.101.9150", path)
    assert (status, previous) == ("same", "32.0.101.9150")
    assert path.stat().st_mtime_ns == mtime  # файл не трогали


def test_driver_version_changed_reports_previous(tmp_path: Path) -> None:
    path = tmp_path / "driver_version"
    check_driver_version("32.0.101.9150", path)
    status, previous = check_driver_version("32.0.101.7020", path)
    assert (status, previous) == ("changed", "32.0.101.9150")
    assert path.read_text(encoding="utf-8").strip() == "32.0.101.7020"


def test_driver_version_empty_file_treated_as_first_run(tmp_path: Path) -> None:
    path = tmp_path / "driver_version"
    path.write_text("", encoding="utf-8")
    status, previous = check_driver_version("32.0.101.9150", path)
    assert (status, previous) == ("saved", None)


# --- CLI: main(["state"]) ---


_DEV = SimpleNamespace(name="Intel Arc B580", device_id=0xE20B)


def _oc_info(step: float = 1.0) -> OcControlInfo:
    return OcControlInfo(
        supported=True, relative=False, units=0, min=0.0, max=100.0, step=step, default=100.0
    )


class _StateApi:
    """Фейк IgclApi для cmd_state: OC-свойства + версия драйвера."""

    def __enter__(self) -> _StateApi:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def read_oc_properties(self, handle: object) -> dict:
        return {"supported": True, "powerLimit": _oc_info(step=1.0)}

    def get_device_properties(self, handle: object) -> SimpleNamespace:
        return SimpleNamespace(name="Intel Arc B580")

    def read_raw_device_properties(self, handle: object) -> SimpleNamespace:
        # (32 << 48) | (0 << 32) | (101 << 16) | 9150
        drv = (32 << 48) | (0 << 32) | (101 << 16) | 9150
        return SimpleNamespace(
            driver_version=drv,
            firmware_version=SimpleNamespace(major_version=1, minor_version=0),
        )


@pytest.fixture()
def state_env(monkeypatch, tmp_path: Path) -> None:
    """Изолирует журнал и файл версии драйвера, подменяет IGCL-слой."""
    monkeypatch.setenv("ARCTUNE_LOG", str(tmp_path / "logbook.jsonl"))
    monkeypatch.setattr("arctuner.igcl.api.IgclApi", lambda: _StateApi())
    monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: (1, _DEV))
    monkeypatch.setattr("arctuner.tuner.current_values", lambda _api, _h: {"powerLimit": 100.0})
    monkeypatch.setattr(
        "arctuner.state_doctor.driver_version_path", lambda: tmp_path / "driver_version"
    )


def test_state_matches_returns_zero(state_env, capsys, tmp_path: Path) -> None:
    """Журнал совпадает с картой — exit 0, драйвер сохраняется при первом запуске."""
    from arctuner.logbook import Logbook

    log = Logbook(tmp_path / "logbook.jsonl")
    log.record("powerLimit", 100.0, 100.0, device="Intel Arc B580")
    assert main(["state"]) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert "совпадает с журналом" in out
    assert "первый запуск" in out


def test_state_drift_returns_one(state_env, capsys, tmp_path: Path) -> None:
    """Расхождение карты и журнала — exit 1 (находка доктора)."""
    from arctuner.logbook import Logbook

    log = Logbook(tmp_path / "logbook.jsonl")
    log.record("powerLimit", 100.0, 120.0, device="Intel Arc B580")
    assert main(["state"]) == 1
    out = capsys.readouterr().out
    assert "[DRIFT]" in out
    assert "сброшено драйвером?" in out


def test_state_driver_change_prints_warning(state_env, capsys, tmp_path: Path) -> None:
    """Смена версии драйвера между сессиями видна в выводе."""
    path = tmp_path / "driver_version"
    path.write_text("32.0.101.7020\n", encoding="utf-8")
    main(["state"])
    out = capsys.readouterr().out
    assert "32.0.101.7020 → 32.0.101.9150" in out
    assert "разгон мог сброситься" in out


def test_state_no_arc_returns_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("arctuner.igcl.api.IgclApi", lambda: _StateApi())
    monkeypatch.setattr("arctuner.telemetry.find_arc_adapter", lambda _api: None)
    assert main(["state"]) == 1
    assert "не найдена" in capsys.readouterr().out


def test_state_empty_journal_returns_zero(state_env, capsys) -> None:
    """Пустой журнал — не ошибка: сверять нечего, exit 0."""
    assert main(["state"]) == 0
    assert "сверять нечего" in capsys.readouterr().out


def test_state_finding_dataclass_is_frozen() -> None:
    f = StateFinding("powerLimit", "ok", 100.0, 100.0, "msg")
    with pytest.raises(Exception):  # noqa: B017 — frozen dataclass
        f.status = "drift"  # type: ignore[misc]
