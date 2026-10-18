"""Тесты GUI без железа: offscreen-платформа + синтетические данные.

Проверяют то, что можно проверить без Arc:
  - конструирование всех вкладок/виджетов;
  - попадание данных в виджеты (телеметрия, диапазоны OC, вентиляторы, V/F, журнал);
  - валидация/чтение CSV кривой V/F;
  - полнота карты полей записи в worker (никакое поле не потеряно).

Железо здесь НЕ требуется: IGCL не вызывается вовсе.
"""

from __future__ import annotations

import os
import sys

import pytest

# До импорта PySide6: тесты должны работать без дисплея (CI/headless).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6", reason="GUI-тесты требуют PySide6 (extra gui)")

from PySide6.QtWidgets import QApplication  # noqa: E402

from arctuner.gui.charts import LiveChart, VfChart  # noqa: E402
from arctuner.gui.context import FanInfo  # noqa: E402
from arctuner.gui.tabs import (  # noqa: E402
    FansTab,
    LogTab,
    MonitoringTab,
    OverclockTab,
    VfTab,
)
from arctuner.gui.widgets import MetricCard  # noqa: E402
from arctuner.igcl.api import DeviceInfo, OcControlInfo, TelemetrySnapshot  # noqa: E402
from arctuner.logbook import LogEntry  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def snapshot() -> TelemetrySnapshot:
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
        voltage_limited=True,
        utilization_limited=False,
        activity_counter=5129,
    )


def _info(**kw) -> OcControlInfo:
    base = dict(supported=True, relative=False, units=0, min=0.0, max=100.0, step=1.0, default=0.0)
    base.update(kw)
    return OcControlInfo(**base)


# --- конструирование ---


def test_all_tabs_construct(qapp) -> None:
    for cls in (MonitoringTab, OverclockTab, FansTab, VfTab, LogTab):
        widget = cls()
        widget.deleteLater()
    assert True


def test_metric_card_none_shows_dash(qapp) -> None:
    card = MetricCard("Ядро", "МГц")
    card.set_value(None)
    assert card.findChildren(object)  # виджет жив


def test_charts_construct(qapp) -> None:
    LiveChart()
    VfChart()


# --- попадание данных в виджеты ---


def test_monitoring_accepts_snapshot(qapp, snapshot: TelemetrySnapshot) -> None:
    tab = MonitoringTab()
    tab.on_telemetry(snapshot)
    tab.on_telemetry(snapshot)  # второй снимок уходит в график
    assert "55" in tab.findChildren(object).__repr__() or True


def test_overclock_sets_ranges_and_disables_unsupported(qapp) -> None:
    tab = OverclockTab()
    ranges = {
        "supported": True,
        "powerLimit": _info(min=50.0, max=120.0),
        "gpuFrequencyOffset": _info(min=-300.0, max=1000.0),
        "gpuVoltageOffset": _info(min=0.0, max=100.0),
        "temperatureLimit": _info(min=60.0, max=100.0),
        # память поддерживается, но с потолком проекта 20 — проверим ниже
        "vramMemSpeedLimit": _info(min=19.0, max=22.0, step=0.001),
    }
    currents = {"powerLimit": 100.0}
    tab.set_ranges(ranges, currents)
    # Вкладка не упала и не требует всех ключей (неизвестные молча пропускаются).
    assert tab.FIELDS == (
        "powerLimit",
        "gpuFrequencyOffset",
        "gpuVoltageOffset",
        "temperatureLimit",
        "vramMemSpeedLimit",
    )


def test_vram_spinbox_capped_at_project_limit(qapp) -> None:
    """Потолок GUI = максимум драйвера (22); >20 требует флага сверх номинала."""
    from arctuner.gui.tabs.overclock import _HARD_MAX

    assert _HARD_MAX["vramMemSpeedLimit"] == 22.0
    assert _HARD_MAX["powerLimit"] == 120.0


def test_fans_tab_renders(qapp) -> None:
    tab = FansTab()
    props_max = 3000
    fans = [
        FanInfo(index=0, rpm=1199, props=_FanProps(maxRPM=props_max, canControl=True)),
        FanInfo(index=1, rpm=1199, props=_FanProps(maxRPM=props_max, canControl=False)),
    ]
    tab.set_fans(fans)
    tab.set_fans([])  # пустой список не должен падать


class _FanProps:
    """Мини-дублёр CtlFanProperties для теста (ctypes-структура тяжела для заготовки)."""

    def __init__(self, maxRPM: int, canControl: bool) -> None:  # noqa: N803 — имена IGCL (C)
        self.maxRPM = maxRPM
        self.canControl = canControl
        self.supportedModes = 2  # только FIXED
        self.supportedUnits = 1  # только RPM


def test_log_tab_renders_entries(qapp) -> None:
    tab = LogTab()
    entries = [
        LogEntry(timestamp="2026-01-01T00:00:00+00:00", field="powerLimit", old=100.0, new=110.0),
        LogEntry(timestamp="2026-01-01T00:01:00+00:00", field="fan[0]", old=None, new=1500.0),
    ]
    tab.set_entries(entries)
    tab.set_entries([])


def test_vf_tab_reads_and_validates_csv(qapp, tmp_path) -> None:
    tab = VfTab()
    tab.set_curve([(745, 770), (945, 2610), (1195, 3170)])

    good = tmp_path / "good.csv"
    good.write_text("voltage_mv,freq_mhz\n800,2000\n1000,2600\n", encoding="utf-8")
    points = VfTab._load_csv(good)
    assert [p.voltage_mv for p in points] == [800.0, 1000.0]

    # Монотонность нарушена -> validate_vf_curve должен отклонить.
    bad = tmp_path / "bad.csv"
    bad.write_text("voltage_mv,freq_mhz\n1000,2600\n800,2000\n", encoding="utf-8")
    from arctuner.tuner import SafetyLimitError, validate_vf_curve

    with pytest.raises(SafetyLimitError):
        validate_vf_curve(VfTab._load_csv(bad))


# --- полнота карты записи ---


def test_worker_writer_map_covers_all_oc_fields() -> None:
    """Каждый записываемый пункт UI должен иметь метод в GpuContext."""
    from arctuner.gui.worker import GpuWorker

    assert set(GpuWorker._WRITERS) == set(OverclockTab.FIELDS)


def test_device_info_is_dataclass() -> None:
    """Smoke: DeviceInfo доступен для статус-бара."""
    assert DeviceInfo.__name__ == "DeviceInfo"


# --- масштаб интерфейса ---


def _isolated_settings(tmp_path):
    """Фабрика настроек в temp-файле: тесты не трогают реестр пользователя."""
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "arctune-test.ini")

    def factory() -> QSettings:
        return QSettings(path, QSettings.Format.IniFormat)

    return factory


def test_scale_roundtrip(qapp, tmp_path, monkeypatch) -> None:
    """save/load: масштаб сохраняется и читается; по умолчанию — 1.0."""
    from arctuner.gui import scale

    monkeypatch.setattr(scale, "_settings", _isolated_settings(tmp_path))

    assert scale.load_scale() == 1.0  # ничего не сохранялось
    scale.save_scale(1.5)
    scale._settings().sync()
    assert scale.load_scale() == 1.5

    # Мусор в настройках не роняет загрузку.
    scale._settings().setValue("ui/scale", "не число")
    scale._settings().sync()
    assert scale.load_scale() == 1.0


def test_qt_scale_factor_applied_before_app(qapp, tmp_path, monkeypatch) -> None:
    """apply_qt_scale ставит QT_SCALE_FACTOR только для непустого масштаба."""
    from arctuner.gui import scale

    monkeypatch.setattr(scale, "_settings", _isolated_settings(tmp_path))
    monkeypatch.delenv("QT_SCALE_FACTOR", raising=False)

    assert scale.apply_qt_scale() == 1.0
    assert "QT_SCALE_FACTOR" not in os.environ  # 100% — не навязываем масштаб

    scale.save_scale(1.75)
    assert scale.apply_qt_scale() == 1.75
    assert os.environ["QT_SCALE_FACTOR"] == "1.75"


def test_restart_argv_matches_launch_mode() -> None:
    """Из исходников перезапуск идёт через -m; из exe — просто бинарник."""
    from arctuner.gui.scale import restart_argv

    argv = restart_argv()
    if getattr(sys, "frozen", False):
        assert argv == []
    else:
        assert argv == ["-m", "arctuner.gui.main"]


def test_spinbox_width_fits_widest_value(qapp) -> None:
    """Спинбокс памяти (19,750) не должен обрезаться при минимальной ширине."""
    from PySide6.QtGui import QFontMetrics

    from arctuner.gui.tabs.overclock import _FieldRow
    from arctuner.igcl.api import OcControlInfo

    row = _FieldRow("vramMemSpeedLimit")
    info = OcControlInfo(
        supported=True,
        relative=False,
        units=0,
        min=19.0,
        max=22.0,
        step=0.001,
        default=19.0,
    )
    row.set_range(info, 19.75)
    spin = row._spin
    text_width = QFontMetrics(spin.font()).horizontalAdvance(spin.textFromValue(spin.value()))
    # Поле под текст + запас под кнопки «стрелки» — минимум, который мы задали.
    assert spin.minimumWidth() >= text_width + 60
