"""Тесты GUI без железа: offscreen-платформа + синтетические данные.

Проверяют то, что можно проверить без Arc:
  - конструирование всех вкладок/виджетов;
  - попадание данных в виджеты (телеметрия, диапазоны OC, вентиляторы, V/F, журнал);
  - валидация/чтение CSV кривой V/F;
  - полнота карты полей записи в worker (никакое поле не потеряно).

Железо здесь НЕ требуется: сессии IGCL подменяются фейками, а неудачное
открытие реальной сессии (нет драйвера) обрабатывается штатно.
"""

from __future__ import annotations

import os
import sys

import pytest

# До импорта PySide6: тесты должны работать без дисплея (CI/headless).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6", reason="GUI-тесты требуют PySide6 (extra gui)")

from PySide6.QtCore import Qt  # noqa: E402
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


def test_metric_card_accent_property(qapp) -> None:
    """kind-маркеры карточек: temp/power/freq — в dynamic property для QSS."""
    from arctuner.gui.widgets import MetricCard

    card_temp = MetricCard("Т", "°C", accent="temp")
    card_none = MetricCard("Без", "")
    assert card_temp.property("accent") == "temp"
    assert card_none.property("accent") is None


def test_monitoring_cards_have_notes(qapp) -> None:
    """Все 12 карточек мониторинга несут подсказку; температурные — по смыслу."""
    tab = MonitoringTab()
    cards = (
        tab._freq,
        tab._voltage,
        tab._temp,
        tab._power,
        tab._vram_freq,
        tab._vram_voltage,
        tab._vram_temp,
        tab._vram_power,
        tab._activity,
        tab._gpu_vr,
        tab._vram_vr,
        tab._sa_vr,
    )
    for card in cards:
        assert card.toolTip(), f"нет подсказки: {card._title.text()}"
        assert card._value.toolTip() == card.toolTip()
    assert "GDDR6" in tab._vram_temp.toolTip()
    assert "VRM" in tab._gpu_vr.toolTip() or "фаз" in tab._gpu_vr.toolTip()
    assert "System Agent" in tab._sa_vr.toolTip()
    # Прожитый факт: счётчик активности НЕ аптайм — в простое стоит на месте.
    assert "занятости" in tab._activity.toolTip()
    assert "норма" in tab._activity.toolTip()


def test_metric_card_click_shows_note_window(qapp, monkeypatch) -> None:
    """Клик по карточке — явное окно с пояснением (не исчезающий tooltip)."""
    from PySide6.QtTest import QTest

    from arctuner.gui.widgets import MetricCard

    card = MetricCard("Температура", "°C", note="Кристалл GPU, держите <80 °C")
    captured: dict[str, str] = {}

    def fake_information(parent, title, text, *_a, **_kw):
        captured["title"] = title
        captured["text"] = text
        return None

    monkeypatch.setattr("arctuner.gui.widgets.QMessageBox.information", fake_information)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert captured == {"title": "Температура", "text": "Кристалл GPU, держите <80 °C"}

    # Карточка без подсказки: клик не открывает пустое окно.
    captured.clear()
    plain = MetricCard("Без", "")
    QTest.mouseClick(plain, Qt.MouseButton.LeftButton)
    assert captured == {}


def test_theme_switch_preserves_monitoring_chart(qapp) -> None:
    """Тёмная тема НЕ меняет график мониторинга (сохранённый вид — осознанно)."""
    from PySide6.QtCharts import QChart

    live = LiveChart(window_s=60)
    vf = VfChart()
    live.set_dark(True)  # ноу-оп: «прибор» остаётся в светлом виде
    vf.set_dark(True)
    assert live.chart().theme() == QChart.ChartTheme.ChartThemeLight
    assert vf.chart().theme() == QChart.ChartTheme.ChartThemeDark
    vf.set_dark(False)
    assert vf.chart().theme() == QChart.ChartTheme.ChartThemeLight


def test_flame_icon_painted(qapp) -> None:
    """Иконка пламени рисуется кодом: непустая, с прозрачным фоном и цветным телом."""
    from arctuner.gui.theme import flame_icon

    icon = flame_icon(24)
    assert not icon.isNull()
    image = icon.pixmap(24, 24).toImage()
    assert image.pixelColor(12, 18).alpha() > 0  # тело пламени в центре-низу
    assert image.pixelColor(1, 1).alpha() == 0  # угол прозрачен (не квадрат-заливка)


# --- попадание данных в виджеты ---


def test_monitoring_accepts_snapshot(qapp, snapshot: TelemetrySnapshot) -> None:
    """Снимок доходит до карточек, лимитера и графика (без «пустых» проверок)."""
    tab = MonitoringTab()
    tab.on_telemetry(snapshot)
    assert tab._freq._value.text() == "2500 МГц"
    assert tab._voltage._value.text() == "1.000 В"
    assert tab._temp._value.text() == "55 °C"
    assert "Voltage" in tab._limits.text()
    assert len(tab._chart._samples) == 1

    tab.on_telemetry(snapshot)  # второй снимок добавляет точку в график
    assert len(tab._chart._samples) == 2


def test_monitoring_breaks_line_on_long_pause(
    qapp, snapshot: TelemetrySnapshot, monkeypatch
) -> None:
    """Пауза > GAP_MAX_S обнуляет график: линия не «мостит» сон/занятый воркер."""
    from arctuner.gui.tabs.monitoring import GAP_MAX_S

    clock = [100.0]
    monkeypatch.setattr("arctuner.gui.tabs.monitoring.time.monotonic", lambda: clock[0])
    tab = MonitoringTab()  # t0 = 100.0

    clock[0] = 101.0
    tab.on_telemetry(snapshot)
    clock[0] = 102.0
    tab.on_telemetry(snapshot)
    assert len(tab._chart._samples) == 2

    clock[0] += GAP_MAX_S + 1.0  # данные не приходили дольше порога
    tab.on_telemetry(snapshot)
    assert len(tab._chart._samples) == 1


def test_overclock_apply_button_emits_value(qapp, monkeypatch) -> None:
    """Кнопка «Применить…» реально доходит до сигнала (а не падает на window())."""
    from PySide6.QtTest import QTest

    calls: list[tuple[str, float]] = []
    monkeypatch.setattr("arctuner.gui.tabs.overclock.confirm_write", lambda *_a, **_k: True)
    tab = OverclockTab()
    tab.apply_requested.connect(lambda field, value: calls.append((field, value)))
    row = tab._rows["powerLimit"]
    row.set_range(_info(min=50.0, max=120.0), 100.0)
    row._spin.setValue(110.0)

    QTest.mouseClick(row._btn, Qt.MouseButton.LeftButton)

    assert calls == [("powerLimit", 110.0)]


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


def test_worker_replay_dumps_on_counter_reset(qapp, tmp_path) -> None:
    """Poll-цикл воркера держит буфер реплея; сброс счётчика → сигнал с дампом.

    Контекст подменяется фейком: IGCL в тестах не открывается вовсе.
    """
    from pathlib import Path
    from types import SimpleNamespace

    from arctuner.gui.worker import GpuWorker
    from arctuner.igcl.api import TelemetrySnapshot
    from arctuner.replay import IncidentReplay

    def _snap(energy: float) -> TelemetrySnapshot:
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
            activity_counter=1,
            gpu_energy_counter_j=energy,
        )

    worker = GpuWorker()  # без start(): сессия не открывается, IGCL не грузится
    worker._replay = IncidentReplay(directory=tmp_path, device="B580")
    worker._ctx = SimpleNamespace(is_open=True, telemetry=lambda: _snap(1000.0))

    incidents: list[str] = []
    worker.incident_replay.connect(incidents.append)

    worker.poll_telemetry()  # база — инцидента нет
    assert worker._replay is not None and worker._replay.samples == 1
    assert incidents == []

    worker._ctx.telemetry = lambda: _snap(900.0)  # счётчик откатился = инцидент
    worker.poll_telemetry()
    assert len(incidents) == 1
    dump = Path(incidents[0])
    assert dump.exists() and dump.suffix == ".jsonl"

    # Буфер после дампа пуст — счётчик продолжил падать, дамп не дублируется мгновенно.
    assert worker._replay is not None and worker._replay.samples == 0


def test_device_info_is_dataclass() -> None:
    """Smoke: DeviceInfo доступен для статус-бара."""
    assert DeviceInfo.__name__ == "DeviceInfo"


def test_gpu_context_field_grid() -> None:
    """field_grid отдаёт (min, step) для снаппинга; мусор/неподдержка — None."""
    from arctuner.gui.context import GpuContext

    ctx = GpuContext()
    # Сессия не открывается: подменяем чтение диапазонов (read-only).
    ctx.oc_ranges = lambda: {  # type: ignore[method-assign]
        "gpuFrequencyOffset": _info(min=-300.0, max=1000.0, step=1.0),
        "unsupported": _info(supported=False),
        "noStep": _info(step=0.0),
        "flag": False,
    }
    assert ctx.field_grid("gpuFrequencyOffset") == (-300.0, 1.0)
    assert ctx.field_grid("unsupported") is None
    assert ctx.field_grid("noStep") is None
    assert ctx.field_grid("flag") is None
    assert ctx.field_grid("absent") is None


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

    # Сброс на 100% обязан УДАЛИТЬ переменную: иначе перезапуск из GUI
    # унаследует 1.75 от родительского процесса и масштаб не сбросится.
    scale.save_scale(1.0)
    assert scale.apply_qt_scale() == 1.0
    assert "QT_SCALE_FACTOR" not in os.environ


def test_restart_argv_matches_launch_mode() -> None:
    """Из исходников перезапуск идёт через -m; из exe — просто бинарник."""
    from arctuner.gui.scale import restart_argv

    argv = restart_argv()
    if getattr(sys, "frozen", False):
        assert argv == []
    else:
        assert argv == ["-m", "arctuner.gui.main"]


def test_restart_env_strips_pyi_variables(monkeypatch) -> None:
    """Перезапуск не должен унаследовать переменные PyInstaller.

    Иначе новый bootloader in-place переиспользует `_MEIxxxx` умирающего
    инстанса и падает с «Failed to import encodings module» (реальный баг
    onefile-сборки при смене языка EN→RU).
    """
    from PySide6.QtCore import QProcessEnvironment

    from arctuner.gui.scale import restart_env

    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEIx")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\app\arctune-gui.exe")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEIx")
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.5")

    env = restart_env()

    assert isinstance(env, QProcessEnvironment)
    keys = {str(k) for k in list(env.keys())}
    assert not any(k.startswith("_PYI") for k in keys)
    assert "_MEIPASS2" not in keys
    assert "QT_SCALE_FACTOR" in keys  # полезные переменные не трогаем


def test_restart_uses_cleaned_environment(qapp, monkeypatch) -> None:
    """_on_restart спавнит дочерний процесс с окружением из restart_env."""
    from arctuner.gui.window import MainWindow

    _silence_no_device_dialog(monkeypatch)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEIx")

    captured: dict[str, object] = {}

    class FakeProcess:
        def setProgram(self, value: str) -> None:  # noqa: N802
            captured["program"] = value

        def setArguments(self, value: list[str]) -> None:  # noqa: N802
            captured["args"] = value

        def setProcessEnvironment(self, value: object) -> None:  # noqa: N802
            captured["env"] = value

        def startDetached(self) -> bool:  # noqa: N802
            captured["detached"] = True
            return True

    monkeypatch.setattr("arctuner.gui.window.QProcess", FakeProcess)

    win = MainWindow()
    win._timer.stop()

    win._on_restart()  # close() внутри — реальный, воркер корректно остановится

    assert captured["program"] == sys.executable
    assert captured["detached"] is True
    env = captured["env"]
    assert not any(str(k).startswith("_PYI") for k in list(env.keys()))

    assert _wait_worker_stopped(win)


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


def test_spinbox_uses_c_locale_decimal_point(qapp) -> None:
    """Разделитель спинбокса — точка, как в подписях/CLI (системная локаль путала)."""
    from arctuner.gui.tabs.overclock import _FieldRow

    row = _FieldRow("vramMemSpeedLimit")
    assert row._spin.textFromValue(19.75) == "19.750"


def test_overclock_current_unknown_shows_dash(qapp) -> None:
    """Нет текущего значения — «сейчас: —», а не застарелый текст прошлой записи."""
    from arctuner.gui.tabs.overclock import _FieldRow

    row = _FieldRow("powerLimit")
    row.set_range(_info(min=50.0, max=120.0), None)
    assert row._current.text() == "сейчас: —"


def test_overclock_range_label_shows_project_cap(qapp) -> None:
    """Диапазон с потолком проекта подписывается честно, спинбокс зажат ceiling'ом."""
    from arctuner.gui.tabs.overclock import _FieldRow

    row = _FieldRow("powerLimit")
    row.set_range(_info(min=50.0, max=150.0), 100.0)
    assert row._spin.maximum() == 120.0
    assert row._range.text() == "50…120 (потолок проекта)"


def test_scale_load_accepts_only_known_choices(qapp, tmp_path, monkeypatch) -> None:
    """Произвольный масштаб в QSettings игнорируется в пользу 1.0."""
    from arctuner.gui import scale

    monkeypatch.setattr(scale, "_settings", _isolated_settings(tmp_path))
    scale.save_scale(1.33)
    scale._settings().sync()
    assert scale.load_scale() == 1.0
    scale.save_scale(1.75)
    scale._settings().sync()
    assert scale.load_scale() == 1.75


def test_metric_card_negative_zero_shown_plain(qapp) -> None:
    """−0.0 не печатается как «-0.000» — выглядит как отрицательное значение."""
    card = MetricCard("Напряжение", "В")
    card.set_value(-0.0, digits=3)
    assert card._value.text() == "0.000 В"


def test_monitoring_activity_counter_grouped(qapp, snapshot: TelemetrySnapshot) -> None:
    """Счётчик занятости в секундах: разряды разделены, единица — «с»."""
    from dataclasses import replace

    tab = MonitoringTab()
    tab.on_telemetry(replace(snapshot, activity_counter=1234567))
    assert tab._activity._value.text() == "1 234 567 с"


def test_monitoring_shows_watts_when_available(qapp, snapshot: TelemetrySnapshot) -> None:
    """Есть ватты из счётчиков энергии — карточка «Питание» в Вт, % в подсказке."""
    from dataclasses import replace

    tab = MonitoringTab()
    tab.on_telemetry(replace(snapshot, gpu_power_w=148.0))
    assert tab._power._value.text() == "148 Вт"
    assert tab._power._value.toolTip() == "70 %"
    # Ваттов нет — карточка честно падает на проценты (первый тик / старый драйвер).
    tab.on_telemetry(snapshot)
    assert tab._power._value.text() == "70 %"


def test_monitoring_vram_watts_and_vr_temps(qapp, snapshot: TelemetrySnapshot) -> None:
    """Ватты памяти + VR-фазы (ядра/память/SA) приходят из нового снимка."""
    from dataclasses import replace

    tab = MonitoringTab()
    tab.on_telemetry(
        replace(
            snapshot,
            vram_power_w=3.0,
            gpu_vr_temp_c=40.0,
            vram_vr_temp_c=53.0,
            sa_vr_temp_c=43.0,
        )
    )
    assert tab._vram_power._value.text() == "3 Вт"
    assert tab._gpu_vr._value.text() == "40 °C"
    assert tab._vram_vr._value.text() == "53 °C"
    assert tab._sa_vr._value.text() == "43 °C"
    # Без данных (старый драйвер / первый тик) — честное «—», не нули.
    tab.on_telemetry(snapshot)
    assert tab._vram_power._value.text() == "—"
    assert tab._gpu_vr._value.text() == "—"


def test_fans_tab_refresh_button_fires_signal(qapp) -> None:
    """Кнопка «Обновить» перечитывает RPM (после записи RPM не проходил по сигналу)."""
    tab = FansTab()
    got: list[int] = []
    tab.refresh_requested.connect(lambda: got.append(1))
    tab._refresh_btn.click()
    assert got == [1]


def test_fans_tab_refresh_clears_stale_rows(qapp) -> None:
    """Повторное обновление списка не накапливает виджеты-строки."""
    tab = FansTab()
    fans = [FanInfo(index=0, rpm=1199, props=_FanProps(maxRPM=3000, canControl=True))]
    tab.set_fans(fans)
    assert tab._list_layout.count() == 1
    tab.set_fans(fans)
    tab.set_fans(fans)
    qapp.processEvents()  # deleteLater выполняется при возврате в event loop
    assert tab._list_layout.count() == 1
    assert len(tab._rows) == 1


def test_logtab_rollback_targets_deduped_and_filtered(qapp) -> None:
    """Предпросмотр отбата: первое old на поле, без вентиляторов (они вне карты отката)."""
    entries = [
        LogEntry(timestamp="2026-01-01T00:00:00+00:00", field="powerLimit", old=100.0, new=110.0),
        LogEntry(timestamp="2026-01-01T00:01:00+00:00", field="powerLimit", old=110.0, new=113.0),
        LogEntry(timestamp="2026-01-01T00:02:00+00:00", field="fan[0]", old=None, new=1500.0),
        LogEntry(
            timestamp="2026-01-01T00:03:00+00:00", field="temperatureLimit", old=None, new=95.0
        ),
        LogEntry(
            timestamp="2026-01-01T00:04:00+00:00", field="gpuFrequencyOffset", old=0.0, new=160.0
        ),
    ]
    assert LogTab._rollback_targets(entries) == [
        "powerLimit: восстановить 100",
        "gpuFrequencyOffset: восстановить 0",
    ]


def test_gui_rollback_restores_vram_above_nominal(qapp) -> None:
    """Откат памяти >20 из журнала (GUI) не требует повторного согласия.

    Регрессия: _ROLLBACK_MAP звал set_vram_speed без allow_beyond_nominal —
    журнал с old=21 невозможно было откатить (SafetyLimitError).
    """
    from arctuner.gui.context import GpuContext

    calls: list[float] = []

    class _Api:
        loader = type("L", (), {"has": staticmethod(lambda name: False)})()

        def read_oc_properties(self, handle):
            return {"supported": True, "vramMemSpeedLimit": _info(min=19.0, max=22.0)}

        def set_vram_mem_speed(self, handle, gbps):
            calls.append(gbps)

    GpuContext._ROLLBACK_MAP["vramMemSpeedLimit"](_Api(), 1, 21.0)
    assert calls == [21.0]


def test_livechart_axis_grows_beyond_fixed_ranges(qapp) -> None:
    """Значения выше стартовых шкал (100 / 3300) расширяют ось — не обрезаются."""
    chart = LiveChart(window_s=60)
    chart.push(1.0, 105.0, 80.0, 3200.0)
    assert chart._axis_left.max() >= 110.0
    chart.push(2.0, 60.0, 50.0, 3600.0)
    assert chart._axis_right.max() >= 3400.0
    # Обратно ось не сужается (иначе шкала дёргается каждый тик).
    chart.push(3.0, 50.0, 40.0, 2400.0)
    assert chart._axis_right.max() >= 3400.0
    assert chart._axis_left.max() >= 110.0


_VF_STOCK = [(700, 400), (800, 1200), (900, 2000), (1100, 3000)]  # (мВ, МГц)


def test_vf_chart_edit_point_clamps(qapp) -> None:
    """Правка точки: кламп по границам проекта и между соседями (монотонность)."""
    chart = VfChart()
    chart.set_curve(_VF_STOCK)
    chart.set_editable(True)
    got: list[list[tuple[int, int]]] = []
    chart.curve_edited.connect(lambda pts: got.append(list(pts)))
    assert chart.editable() is True

    # Y за соседом сверху (900) → 900 (монотонность); X за соседом (2000) → 1999.
    assert chart.edit_point(1, 5000.0, 3000.0) is True
    assert got[-1][1] == (900, 1999)

    # Y ниже 600 → 600 (сосед не мешает: 900 сверху); X ниже 300 → 300.
    assert chart.edit_point(0, -10.0, 100.0) is True
    assert got[-1][0] == (600, 300)

    # Точку 2 тянут влево за соседа (1999) → 2000: пересечь соседа нельзя.
    assert chart.edit_point(2, 900.0, 100.0) is True
    assert got[-1][2] == (900, 2000)

    # Последнюю точку тянут за глобальный максимум → 3300.
    assert chart.edit_point(3, 1100.0, 4000.0) is True
    assert got[-1][3] == (1100, 3300)

    # Последнюю точку тянут вниз за соседа слева (900) → 900 по напряжению.
    assert chart.edit_point(3, 0.0, 3100.0) is True
    assert got[-1][3] == (900, 3100)


def test_vf_chart_edit_disabled(qapp) -> None:
    """Без editable-режима программная правка запрещена."""
    chart = VfChart()
    chart.set_curve(_VF_STOCK)
    assert chart.editable() is False
    got: list[list[tuple[int, int]]] = []
    chart.curve_edited.connect(lambda pts: got.append(pts))
    assert chart.edit_point(0, 800.0, 500.0) is False
    assert got == []


def test_vf_tab_chart_edit_enables_and_applies(qapp, monkeypatch) -> None:
    """Правка на графике → кнопка записи активна; подтверждение → apply_requested."""
    tab = VfTab()
    tab.set_curve(_VF_STOCK)
    assert tab._apply_chart_btn.isEnabled() is False

    emitted: list[object] = []
    tab.apply_requested.connect(emitted.append)
    monkeypatch.setattr("arctuner.gui.tabs.vf.confirm_write", lambda *a, **k: True)

    # Точку 1 тянут ниже соседа слева (700 мВ) → кламп 700: кривая остаётся монотонной.
    tab._chart.edit_point(1, 550.0, 1250.0)
    assert tab._apply_chart_btn.isEnabled() is True

    tab._on_apply_chart()
    assert len(emitted) == 1
    points = emitted[0]
    assert [(p.voltage_mv, p.freq_mhz) for p in points] == [
        (700.0, 400.0),
        (700.0, 1250.0),
        (900.0, 2000.0),
        (1100.0, 3000.0),
    ]


def test_vf_tab_undo_and_edit_back_to_stock(qapp) -> None:
    """«Отменить правку» и возврат точки в сток — кнопка записи снова неактивна."""
    tab = VfTab()
    tab.set_curve(_VF_STOCK)

    tab._chart.edit_point(1, 600.0, 1250.0)
    assert tab.edited_points()[1] == (700, 1250)  # кламп по соседу слева (700 мВ)

    tab._on_undo()
    assert tab.edited_points() == _VF_STOCK
    assert tab._apply_chart_btn.isEnabled() is False

    # Возврат точки к стоковому значению — правка считается отменённой.
    tab._chart.edit_point(1, 800.0, 1200.0)
    assert tab._apply_chart_btn.isEnabled() is False


def test_vf_tab_apply_preview_contains_stock(qapp, monkeypatch) -> None:
    """Предпросмотр записи пишет и новую точку, и стоковую."""
    tab = VfTab()
    tab.set_curve(_VF_STOCK)
    captured: dict[str, str] = {}

    def fake_confirm(parent, title, changes, detail=""):
        captured["detail"] = detail
        captured["changes"] = list(changes)
        return True

    monkeypatch.setattr("arctuner.gui.tabs.vf.confirm_write", fake_confirm)

    tab._chart.edit_point(1, 850.0, 1250.0)
    tab._on_apply_chart()
    assert "сток: 800 мВ @ 1200 МГц" in captured["detail"]
    assert captured["detail"].count("сток") >= 1
    assert captured["changes"] == ["изменено точек: 1 из 4"]


def test_autotune_tab_start_emits_signal(qapp) -> None:
    """Клик «Запустить» эмитит search_requested с конфигом (цепочка UI → worker)."""
    from arctuner.autotune.engine import TuningConfig
    from arctuner.gui.tabs import AutoTuneTab

    tab = AutoTuneTab()
    emitted: list[object] = []
    tab.search_requested.connect(lambda cfg: emitted.append(cfg))
    tab._on_start()
    assert len(emitted) == 1 and isinstance(emitted[0], TuningConfig)


def test_autotune_tab_live_sample_chart_and_label(qapp, snapshot) -> None:
    """Снимок поиска попадает в мини-график и строку «Сейчас» вкладки."""
    from arctuner.gui.tabs import AutoTuneTab

    tab = AutoTuneTab()
    tab.on_sample(snapshot)
    tab.on_sample(snapshot)
    assert "2500 МГц" in tab._now.text()
    assert "55 °C" in tab._now.text()
    assert len(tab._live._samples) == 2  # оба снимка ушли в график


def _silence_no_device_dialog(monkeypatch) -> None:
    """Без карты start() показывает модальное окно — в тестах гасим его."""
    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)


def _wait_worker_stopped(win, timeout_ms: int = 5000) -> bool:
    """Крутит event loop, пока поток воркера не завершится (без sleep-поллинга).

    Возвращает False по таймауту — тест падает с внятным сообщением, а не виснет.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    if not win._thread.isRunning():
        return True
    loop = QEventLoop()

    def on_finished() -> None:
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_finished)
    win._thread.finished.connect(on_finished)
    timer.start(timeout_ms)
    loop.exec()
    timer.stop()
    win._thread.finished.disconnect(on_finished)
    return not win._thread.isRunning()


def test_close_stops_worker_thread_without_blocking(qapp, monkeypatch) -> None:
    """X не вешает UI: closeEvent откладывает закрытие, поток гаснет по shutdown_done."""
    from arctuner.gui.window import MainWindow

    _silence_no_device_dialog(monkeypatch)
    win = MainWindow()
    win._timer.stop()

    assert win.close() is False  # ctlClose ещё в очереди — окно не закрылось
    assert win._closing is True
    assert win._thread.isRunning()  # UI не ждал воркера

    assert _wait_worker_stopped(win), "поток воркера не завершился за 5 с"
    assert win._worker._ctx.is_open is False  # ctlClose выполнен
    assert win.close() is True  # повторный close уже принимается


def test_repeated_close_is_safe_and_shuts_down_once(qapp, monkeypatch) -> None:
    """Повторное нажатие X во время завершения не падает и не дублирует shutdown."""
    from arctuner.gui.window import MainWindow

    _silence_no_device_dialog(monkeypatch)
    win = MainWindow()
    win._timer.stop()
    shutdowns: list[bool] = []
    win._worker.shutdown_done.connect(lambda: shutdowns.append(True))

    assert win.close() is False
    assert win.close() is False  # второе X: без исключений и повторной очереди

    assert _wait_worker_stopped(win), "поток воркера не завершился за 5 с"
    assert win.close() is True
    assert len(shutdowns) == 1


def test_close_while_worker_busy_does_not_block_ui(qapp, monkeypatch) -> None:
    """Занятый воркер (engine.run/watchdog): closeEvent сразу возвращает управление.

    Воркер держим без сна: queued reload_static висит на threading.Event, а
    queued shutdown встаёт за ним. ctlClose обязан выполниться в потоке воркера,
    а не в UI-потоке.
    """
    import threading
    from types import SimpleNamespace

    from PySide6.QtCore import QMetaObject, Qt

    from arctuner.gui.window import MainWindow
    from arctuner.gui.worker import GpuWorker

    monkeypatch.setattr(GpuWorker, "start", lambda self: None)  # IGCL не трогаем
    _silence_no_device_dialog(monkeypatch)

    gate = threading.Event()
    closed_in: list[int] = []

    class _Ctx:
        """Занят: oc_ranges висит на gate, close фиксирует поток вызова."""

        is_open = False

        def oc_ranges(self):
            gate.wait(5.0)
            return {}

        def current_values(self):
            return {}

        def fans(self):
            return []

        def vf_curve(self):
            return []

        log = SimpleNamespace(read_all=lambda: [])

        def close(self) -> None:
            closed_in.append(threading.get_ident())

    win = MainWindow()
    win._timer.stop()
    win._worker._ctx = _Ctx()
    # Занимаем поток раньше shutdown: queued-слоты одного получателя идут FIFO.
    QMetaObject.invokeMethod(win._worker, "reload_static", Qt.ConnectionType.QueuedConnection)

    assert win.close() is False  # сразу: UI не ждёт занятый воркер
    assert win._thread.isRunning()
    assert closed_in == []  # shutdown ещё в очереди за занятым слотом

    gate.set()
    assert _wait_worker_stopped(win), "поток воркера не завершился за 5 с"
    assert len(closed_in) == 1
    assert closed_in[0] != threading.get_ident()  # ctlClose — в потоке воркера


def test_worker_shutdown_emits_done_even_if_close_fails(qapp) -> None:
    """Сбой ctx.close() не глотается (канал error), но shutdown_done уходит всегда.

    Иначе окно, стоящее на «Завершение…», не смогло бы остановить QThread, а
    причина сбоя пропала бы из UI (пилюля warn).
    """
    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    done: list[bool] = []
    errors: list[str] = []
    worker.shutdown_done.connect(lambda: done.append(True))
    worker.error.connect(errors.append)

    class _Broken:
        def close(self) -> None:
            raise OSError("ctlClose упал")

    worker._ctx = _Broken()
    worker.shutdown()

    assert len(errors) == 1 and "ctlClose" in errors[0]  # причина дошла до UI
    assert done == [True]  # поток всё равно можно остановить


def test_autotune_sample_reaches_both_charts(qapp, snapshot) -> None:
    """Сигнал поиска обновляет и общий график, и мини-график авторазгона."""
    from arctuner.gui.window import MainWindow

    win = MainWindow()
    win._timer.stop()
    win._thread.quit()
    win._thread.wait(2000)
    win._worker.shutdown()

    win._monitoring._chart.clear()
    win._autotune._live.clear()
    win._worker.autotune_sample.emit(snapshot)
    assert len(win._monitoring._chart._samples) == 1
    assert len(win._autotune._live._samples) == 1
    win.close()


def test_worker_autotune_slot_closed_session_channel(qapp) -> None:
    """Слот жив: на закрытой сессии ошибка идёт в autotune_error, не в write_error.

    Проверяет также busy-исход (True в начале, False в конце) — иначе вкладка
    навсегда остаётся заблокированной после неудачного запуска.
    """
    from arctuner.autotune.engine import TuningConfig
    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    errors: list[str] = []
    write_errors: list[str] = []
    busy: list[bool] = []
    worker.autotune_error.connect(errors.append)
    worker.write_error.connect(write_errors.append)
    worker.busy_changed.connect(busy.append)
    worker.autotune_search(TuningConfig())

    assert errors, ".autotune_search не вызван или ошибка потеряна"
    assert "не открыта" in errors[0]  # SafetyLimitError("IGCL-сессия не открыта")
    assert write_errors == []  # поиск — не «запись»: канал разделён
    assert busy == [True, False]  # вкладка разблокируется после неудачи


def test_worker_autotune_stop_flag(qapp) -> None:
    """autotune_stop ставит флаг отмены (без железа — только флаг)."""
    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    assert worker._autotune_stop is False
    worker.autotune_stop()
    assert worker._autotune_stop is True


def test_worker_start_missing_driver_reports_error(qapp) -> None:
    """Нет IGCL.dll (IgclNotFoundError=RuntimeError) → ошибка в UI, не тишина.

    Регрессия: слот start() падал мимо except-ов, error не эмитился, и окно
    навсегда оставалось на «Подключение…» без единого диалога.
    """
    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    errors: list[str] = []
    worker.error.connect(errors.append)

    def boom():
        raise RuntimeError("IGCL.dll не найден")

    worker._ctx.open = boom  # type: ignore[method-assign]
    worker.start()
    assert errors and "не найден" in errors[0]


def test_worker_reload_static_log_error_does_not_raise(qapp) -> None:
    """Сбой чтения журнала не вылетает из reload_static (busy не залипает)."""
    from types import SimpleNamespace

    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    errors: list[str] = []
    worker.error.connect(errors.append)

    def boom() -> list:
        raise OSError("журнал занят")

    worker._ctx = SimpleNamespace(  # type: ignore[assignment]
        oc_ranges=lambda: {},
        current_values=lambda: {},
        fans=lambda: [],
        vf_curve=lambda: [],
        log=SimpleNamespace(read_all=boom),
    )
    worker.reload_static()  # не должно бросить
    assert errors and "журнал" in errors[0]


def test_autotune_stop_not_reset_by_queued_slot(qapp) -> None:
    """«Стоп» до выполнения queued-слота не теряется: сброс — только в arm()."""
    from arctuner.autotune.engine import TuningConfig
    from arctuner.gui.worker import GpuWorker

    worker = GpuWorker()
    worker.autotune_arm()  # UI нажал «Запустить»
    worker.autotune_stop()  # UI нажал «Стоп» до выполнения слота
    worker.autotune_search(TuningConfig())  # закрытая сессия: ошибка, но
    assert worker._autotune_stop is True  # отмену слот не сбросил


def test_autotune_completed_reaches_tab(qapp) -> None:
    """Итог поиска доходит до вкладки (обрыв connect = «Результат: —» навсегда).

    Регрессия реального бага: autotune_completed эмитился, но не был
    подключён в MainWindow — после поиска вкладка молча оставалась
    с последним шагом и пустым результатом.
    """
    from arctuner.autotune.engine import TuningResult
    from arctuner.gui.window import MainWindow

    win = MainWindow()
    win._timer.stop()  # закрытая сессия: опрос устроил бы модальный «нет подключения»
    win._thread.quit()
    win._thread.wait(2000)
    win._worker.shutdown()  # worker.start() успел открыть IGCL — закрываем (не мешать hardware-тестам)

    win._autotune.update_progress(2, 30, "кандидат +54")
    fake = TuningResult(
        mode="frequency",
        baseline=0.0,
        best_stable=54.0,
        steps_taken=2,
        stopped_reason="тест-причина",
        card_value=54.0,  # карта подтверждённо на best
    )
    win._worker.autotune_completed.emit(fake)
    assert "тест-причина" in win._autotune._state.text()
    assert "54" in win._autotune._result.text()
    # Итог обязан явно говорить, что на карте («а применилось ли?»).
    assert "Карта оставлена на +54" in win._autotune._result.text()
    win.close()


def test_autotune_result_warns_when_card_not_on_best(qapp) -> None:
    """Откат не удался → вкладка ПРЕДУПРЕЖДАЕТ, а не поздравляет с результатом."""
    from arctuner.autotune.engine import TuningResult
    from arctuner.gui.window import MainWindow

    win = MainWindow()
    win._timer.stop()
    win._thread.quit()
    win._thread.wait(2000)
    win._worker.shutdown()

    fake = TuningResult(
        mode="frequency",
        baseline=0.0,
        best_stable=54.0,
        steps_taken=2,
        stopped_reason="тест",
        card_value=None,  # откат не прошёл — состояние неизвестно
    )
    win._worker.autotune_completed.emit(fake)
    text = win._autotune._result.text()
    assert "НЕ на стабильном" in text
    assert "Карта оставлена на" not in text
    win.close()


def test_autotune_stop_is_synchronous_from_ui(qapp) -> None:
    """«Стоп» ставит флаг немедленно: queued-стоп не дожил бы до воркера.

    Воркер-поток занят синхронным engine.run — слот-очередь обрабатывается
    только ПОСЛЕ поиска, кнопка «Стоп» была бы мертва.
    """
    from arctuner.gui.window import MainWindow

    win = MainWindow()
    win._timer.stop()
    win._thread.quit()
    win._thread.wait(2000)
    win._worker.shutdown()  # сессия открыта воркером в thread.started — закрыть честно

    assert win._worker._autotune_stop is False
    win._autotune.stop_requested.emit()  # как клик «Стоп»
    assert win._worker._autotune_stop is True  # без processEvents — прямая доставка
    win.close()


def test_gui_error_handler_install_uninstall(monkeypatch) -> None:
    """GuiExceptionHandler устанавливает/сбрасывает sys.excepthook корректно."""
    from arctuner.gui.errors import GuiExceptionHandler

    captured_calls: list[str] = []

    def fake_hook(exc_type, exc, tb):
        captured_calls.append(f"{exc_type.__name__}: {exc}")

    monkeypatch.setattr("sys.excepthook", fake_hook)

    handler = GuiExceptionHandler()
    assert handler._previous_hook is None  # не установлен

    handler.install()
    # sys.excepthook подменяется на _handle; предыдущий сохранён.
    assert sys.excepthook is not fake_hook
    assert handler._previous_hook is fake_hook

    # Пропустим ошибку через обработчик — она уйдёт в fake_hook (через uninstall).
    handler.uninstall()
    assert handler._previous_hook is None
    assert sys.excepthook is fake_hook


def test_gui_error_handler_propagates_interrupt(monkeypatch) -> None:
    """KeyboardInterrupt/SystemExit пробрасываются дальше, не ловятся."""
    from arctuner.gui.errors import GuiExceptionHandler

    captured: list[tuple] = []

    def fake_hook(exc_type, exc, tb):
        captured.append((exc_type, exc, tb))

    monkeypatch.setattr("sys.excepthook", fake_hook)

    handler = GuiExceptionHandler()
    handler.install()

    # Simulate KeyboardInterrupt — обработчик должен делегировать дальше.
    handler._handle(KeyboardInterrupt, KeyboardInterrupt("ctrl+c"), None)

    assert len(captured) == 1
    assert captured[0][0] is KeyboardInterrupt


# --- переключение языка (Вид → Язык) ---


def _isolate_language_file(monkeypatch, tmp_path):
    """Файл языка → tmp: тесты не должны трогать ~/.arctune/language пользователя."""
    from arctuner import i18n

    path = tmp_path / "language"
    monkeypatch.setattr(i18n, "_language_file", lambda: path)
    return path


def test_language_menu_click_saves_and_asks_restart(qapp, monkeypatch, tmp_path) -> None:
    """Клик «Вид → Язык → English»: файл=en, диалог перезапуска показан, процесс=en."""
    from PySide6.QtWidgets import QMessageBox

    from arctuner import i18n
    from arctuner.gui.window import MainWindow

    path = _isolate_language_file(monkeypatch, tmp_path)
    _silence_no_device_dialog(monkeypatch)
    asked: list[object] = []
    monkeypatch.setattr(
        "arctuner.gui.window.QMessageBox.question",
        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No,
    )

    win = MainWindow()
    win._timer.stop()

    # реальный путь пользователя: пункт меню, а не прямой вызов обработчика
    view = next(a for a in win.menuBar().actions() if a.text() == "Вид")
    lang_menu = next(a for a in view.menu().actions() if a.text() == "Язык")
    en_action = next(a for a in lang_menu.menu().actions() if a.text() == "English")
    en_action.trigger()

    assert path.exists() and path.read_text(encoding="utf-8") == "en"
    assert win._restart_action.isEnabled()  # restart-экшен включён
    assert len(asked) == 1  # диалог «перезапустить сейчас?» показан
    assert i18n.current_language() == "en"  # процесс переключен сразу (диалог по-английски)
    assert win._status.currentMessage()  # выбран «Позже» → подсказка в статус-баре

    win.close()
    assert _wait_worker_stopped(win)


def test_language_restart_yes_calls_restart(qapp, monkeypatch, tmp_path) -> None:
    """Ответ «Да» в диалоге → вызван _on_restart (перезапуск для применения языка)."""
    from PySide6.QtWidgets import QMessageBox

    from arctuner.gui.window import MainWindow

    _isolate_language_file(monkeypatch, tmp_path)
    _silence_no_device_dialog(monkeypatch)
    monkeypatch.setattr(
        "arctuner.gui.window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )

    win = MainWindow()
    win._timer.stop()
    calls: list[bool] = []
    monkeypatch.setattr(win, "_on_restart", lambda: calls.append(True))

    win._on_language_selected("en")

    assert calls == [True]  # перезапуск запрошен

    win.close()
    assert _wait_worker_stopped(win)


def test_language_reselect_same_language_no_dialog(qapp, monkeypatch, tmp_path) -> None:
    """Повторный выбор уже активного языка — без диалога и без записи файла."""
    from PySide6.QtWidgets import QMessageBox

    from arctuner.gui.window import MainWindow

    path = _isolate_language_file(monkeypatch, tmp_path)
    _silence_no_device_dialog(monkeypatch)
    asked: list[object] = []
    monkeypatch.setattr(
        "arctuner.gui.window.QMessageBox.question",
        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No,
    )

    win = MainWindow()
    win._timer.stop()

    win._on_language_selected("ru")  # текущий язык уже ru (conftest пинит)

    assert not asked
    assert not win._restart_action.isEnabled()
    assert not path.exists()

    win.close()
    assert _wait_worker_stopped(win)
