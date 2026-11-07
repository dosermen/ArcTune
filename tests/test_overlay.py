"""Тесты оверлея без железа: offscreen-платформа + синтетическая телеметрия.

Проверяется то, что можно без Arc:
  - конструирование и переключение макетов (набор показанных ячеек);
  - свой набор метрик (галочки вкладки) и защита от пустого HUD;
  - попадание данных снимка в ячейки (и игнорирование, пока окно спрятано);
  - сохранение выбора пользователя (вкл/макет/метрики/непрозрачность) + защита от мусора;
  - read-only инвариант: в окне нет ни одной кнопки записи;
  - связка «вкладка Оверлей ↔ меню «Вид» ↔ контекстное меню HUD» (одни настройки).

Сессия IGCL оверлеем не открывается: снимки приходят сигналами GpuWorker,
поэтому здесь достаточно эмита сигнала в UI-потоке.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import ClassVar

import pytest

# До импорта PySide6: тесты должны работать без дисплея (CI/headless).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6", reason="GUI-тесты требуют PySide6 (extra gui)")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402

from arctuner.cpu import CpuSnapshot  # noqa: E402
from arctuner.gui import overlay  # noqa: E402
from arctuner.gui.minichart import MiniChart  # noqa: E402
from arctuner.gui.overlay import (  # noqa: E402
    DEFAULT_MINI_CHART_SERIES,
    DEFAULT_MINI_CHART_WINDOW,
    LAYOUTS,
    OPACITIES,
    OverlayWindow,
)
from arctuner.igcl.api import TelemetrySnapshot  # noqa: E402


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


class _FakeSettings:
    """Подмена QSettings: значения живут в словаре процесса (реестр не трогаем)."""

    _store: ClassVar[dict[str, object]] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def value(self, key: str, default: object = None) -> object:
        return self._store.get(key, default)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802 (Qt-имя)
        self._store[key] = value


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    _FakeSettings._store.clear()
    monkeypatch.setattr(overlay, "QSettings", _FakeSettings)
    return _FakeSettings()


def _close_window(win) -> None:
    """Закрывает окно и ждёт конца воркера — тест падает, а не висит."""
    win._timer.stop()
    win.close()
    if not win._thread.isRunning():
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    win._thread.finished.connect(loop.quit)
    timer.start(5000)
    loop.exec()
    timer.stop()
    assert not win._thread.isRunning(), "поток воркера не завершился за 5 с"


# --- конструирование и макеты ---


def test_overlay_constructs_with_defaults(settings, qapp) -> None:
    """Без настроек — полоса 1×6 и 85 %; окно само по себе не показывается."""
    win = OverlayWindow()
    assert win.layout_name() in LAYOUTS
    assert win.layout_name() == overlay.DEFAULT_LAYOUT
    assert win.orientation() == "horizontal"
    assert win.current_opacity() in OPACITIES
    assert win.current_opacity() == overlay.DEFAULT_OPACITY
    assert not win.isVisible()  # пользователь решает, показывать ли HUD
    win.deleteLater()


def test_overlay_orientation_switches_between_row_and_column(settings, qapp) -> None:
    """Горизонтальная раскладка по умолчанию; вертикальная складывает метрики в колонку."""
    win = OverlayWindow()
    power_index = win._grid.indexOf(win._cells["power"])
    assert win._grid.getItemPosition(power_index)[:2] == (0, 1)

    fired: list[str] = []
    win.orientation_changed.connect(fired.append)
    win.set_orientation("vertical")
    power_index = win._grid.indexOf(win._cells["power"])
    assert win._grid.getItemPosition(power_index)[:2] == (1, 0)
    assert win.orientation() == "vertical"
    assert settings._store["overlay/orientation"] == "vertical"

    win.set_orientation("horizontal")
    power_index = win._grid.indexOf(win._cells["power"])
    assert win._grid.getItemPosition(power_index)[:2] == (0, 1)
    assert fired == ["vertical", "horizontal"]
    with pytest.raises(ValueError, match="неизвестная ориентация"):
        win.set_orientation("diagonal")
    win.deleteLater()


def test_overlay_layout_switch_rebuilds_grid(settings, qapp) -> None:
    """Макет выбирает набор ячеек; смена сохраняется и эмитится один раз."""
    win = OverlayWindow()
    fired: list[str] = []
    win.layout_changed.connect(fired.append)

    # компактный макет: VR-фазы и плашка ограничений спрятаны
    assert not win._cells["temp"].isHidden()
    assert win._cells["gpu_vr"].isHidden()
    assert win._limits.isHidden()

    win.set_layout("grid")
    assert win.layout_name() == "grid"
    assert settings._store["overlay/layout"] == "grid"
    assert not win._cells["freq"].isHidden()  # основные ячейки остались
    assert win._cells["gpu_vr"].isHidden()  # VR-фазы — только в подробном
    assert win._limits.isHidden()  # а плашка — тоже только там

    win.set_layout("detailed")
    assert not win._limits.isHidden()
    assert fired == ["grid", "detailed"]

    win.set_layout("detailed")  # тот же макет — no-op, сигнал не повторяется
    assert fired == ["grid", "detailed"]
    win.deleteLater()


def test_overlay_rejects_unknown_layout_and_opacity(settings, qapp) -> None:
    """Мусорный макет/непрозрачность не принимаются и не пишутся в настройки."""
    win = OverlayWindow()
    with pytest.raises(ValueError, match=r"неизвестный макет"):
        win.set_layout("hud")
    with pytest.raises(ValueError, match=r"непрозрачность"):
        win.set_opacity(0.6)
    assert win.layout_name() == overlay.DEFAULT_LAYOUT
    assert "overlay/layout" not in settings._store
    win.deleteLater()


def test_overlay_custom_cells_rebuild_grid(settings, qapp) -> None:
    """Свой набор метрик: канонический порядок, пересборка, сохранение, no-op."""
    win = OverlayWindow()
    fired: list[tuple[str, ...]] = []
    win.cells_changed.connect(fired.append)

    win.set_visible_cells(["vram", "temp"])  # порядок входа не важен
    assert win.visible_cells() == ("temp", "vram")  # канонический порядок отрисовки
    assert settings._store["overlay/cells"] == "temp,vram"
    assert not win._cells["temp"].isHidden()
    assert not win._cells["vram"].isHidden()
    assert win._cells["power"].isHidden()  # выбранной метрики нет в HUD
    assert fired == [("temp", "vram")]

    win.set_visible_cells(("temp", "vram"))  # тот же набор — no-op, сигнал не повторяется
    assert fired == [("temp", "vram")]

    with pytest.raises(ValueError, match=r"хотя бы одна"):
        win.set_visible_cells([])
    with pytest.raises(ValueError, match=r"неизвестные метрики"):
        win.set_visible_cells(["temp", "nope"])
    assert win.visible_cells() == ("temp", "vram")  # ошибки не портят состояние
    assert "overlay/cells" in settings._store
    win.deleteLater()


def test_overlay_layout_preset_restores_cells(settings, qapp) -> None:
    """Пресет макета возвращает свой набор метрик — подписи «1×6» и т.п. не врут."""
    win = OverlayWindow()
    win.set_visible_cells(["temp"])

    win.set_layout("grid")
    assert win.visible_cells() == ("temp", "power", "freq", "load", "vram", "vram_used")
    assert settings._store["overlay/cells"] == "temp,power,freq,load,vram,vram_used"
    assert not win._cells["temp"].isHidden()
    assert not win._cells["vram_used"].isHidden()  # (isHidden, т.к. окно не показано)
    win.deleteLater()


def test_overlay_opacity_roundtrip(settings, qapp) -> None:
    """Непрозрачность применяется к окну, сохраняется и эмитится сигналом."""
    win = OverlayWindow()
    fired: list[float] = []
    win.opacity_changed.connect(fired.append)

    win.set_opacity(0.5)
    assert win.current_opacity() == 0.5
    assert win.windowOpacity() == pytest.approx(0.5, abs=1 / 255)  # квантование 8 бит
    assert settings._store["overlay/opacity"] == 0.5
    assert fired == [0.5]

    win.set_opacity(0.5)  # повтор — не эмитим
    assert fired == [0.5]
    win.deleteLater()


# --- данные ---


def test_overlay_telemetry_fills_cells(settings, qapp, snapshot) -> None:
    """Снимок попадает в ячейки; спрятанный оверлей перерисовке не мучается."""
    win = OverlayWindow()
    win.show()
    win.on_telemetry(snapshot)

    assert win._cells["temp"].text() == "55 °C"
    assert win._cells["power"].text() == "70 %"  # ватт ещё нет (первый тик)
    assert win._cells["freq"].text() == "2500 МГц"
    assert win._cells["vram"].text() == "19.75 Гбит/с"

    win.set_shown(False)
    win.on_telemetry(replace(snapshot, gpu_temp_c=99.0))
    assert win._cells["temp"].text() == "55 °C"  # окно спрятано — не обновлялось
    win.deleteLater()


def test_overlay_shows_watts_when_available(settings, qapp, snapshot) -> None:
    """Со 2-го тика PowerMeter даёт ватты; процент уходит в tooltip ячейки."""
    win = OverlayWindow()
    win.show()
    win.on_telemetry(replace(snapshot, gpu_power_w=145.0))

    assert win._cells["power"].text() == "145 Вт"
    assert win._cells["power"]._value.toolTip() == "70 %"
    win.deleteLater()


def test_overlay_shows_load_and_vram_usage(settings, qapp, snapshot) -> None:
    """Ячейки load/vram_used: % LoadMeter и занятость в ГиБ, итог — в подсказке."""
    win = OverlayWindow()
    win.show()
    win.on_telemetry(snapshot)  # база LoadMeter не накоплена — честное «—», не ноль
    assert win._cells["load"].text() == "—"
    assert win._cells["vram_used"].text() == "—"

    win.on_telemetry(
        replace(
            snapshot,
            gpu_load_percent=10.3,
            vram_used_bytes=8 * 1024**3,
            vram_total_bytes=12 * 1024**3,
            vram_percent=68.7,
        )
    )
    assert win._cells["load"].text() == "10 %"  # в HUD дробь не нужна
    assert win._cells["vram_used"].text() == "8.00 ГиБ"
    assert win._cells["vram_used"]._value.toolTip() == "всего 12.00 ГиБ · 69 %"
    win.deleteLater()


def test_overlay_media_cell_is_optional(settings, qapp, snapshot) -> None:
    """Ячейка «Медиа» есть в списке метрик, но вне пресетов; данные — из LoadMeter."""
    win = OverlayWindow()
    win.show()
    # По умолчанию (пресет полосы) ячейки нет — опциональная метрика выключена.
    assert "media_load" not in win.visible_cells()

    win.set_visible_cells(("load", "media_load"))
    win.on_telemetry(snapshot)  # база LoadMeter не накоплена — честное «—», не ноль
    assert win._cells["media_load"].text() == "—"

    win.on_telemetry(replace(snapshot, media_load_percent=3.7))
    assert win._cells["media_load"].text() == "4 %"  # в HUD дробь не нужна
    win.deleteLater()


def test_overlay_detailed_limits_highlight(settings, qapp, snapshot) -> None:
    """Плашка ограничений красится в предупреждение только при активном лимите."""
    win = OverlayWindow()
    win.show()
    win.set_layout("detailed")
    win.on_telemetry(snapshot)  # voltage_limited=True в фикстуре

    assert win._limits.property("active") == "true"
    assert "Voltage" in win._limits.text()

    win.on_telemetry(replace(snapshot, voltage_limited=False))
    assert win._limits.property("active") == "false"
    assert "нет" in win._limits.text()
    win.deleteLater()


def test_mini_chart_splits_lines_on_missing_samples(qapp, snapshot) -> None:
    """Мини-график не соединяет линией точки через отсутствующую телеметрию."""
    chart = MiniChart(30)
    chart.set_series(("temp",))
    chart.push(1.0, snapshot)
    chart.push(2.0, replace(snapshot, gpu_temp_c=56.0))
    assert len(chart._series["temp"]) == 1
    assert chart._series["temp"][0].count() == 2

    chart.push(3.0, replace(snapshot, gpu_temp_c=None))
    chart.push(4.0, replace(snapshot, gpu_temp_c=60.0))
    assert len(chart._series["temp"]) == 2
    assert chart._series["temp"][0].count() == 2
    assert chart._series["temp"][1].count() == 1
    chart.deleteLater()


def test_mini_chart_splits_lines_after_long_gap(qapp, snapshot) -> None:
    """После длительной паузы новая точка начинает отдельный отрезок."""
    chart = MiniChart(30)
    chart.set_series(("freq",))
    chart.push(1.0, snapshot)
    chart.push(10.0, replace(snapshot, gpu_freq_mhz=2600.0))
    assert len(chart._series["freq"]) == 2
    assert chart._series["freq"][0].count() == 1
    assert chart._series["freq"][1].count() == 1
    chart.deleteLater()


def test_mini_chart_inspector_reports_nearest_sample(qapp, snapshot) -> None:
    """Курсор-инспектор возвращает значения ближайшей точки на выбранных сериях."""
    chart = MiniChart(30)
    chart.set_series(("temp", "freq"))
    chart.push(1.0, snapshot)
    chart.push(2.0, replace(snapshot, gpu_temp_c=61.0, gpu_freq_mhz=2700.0))

    text = chart.inspect_at_time(1.8)
    assert "−0.0 с" in text
    assert "Температура: 61 °C" in text
    assert "Частота: 2700 МГц" in text
    assert chart.inspect_at_time(100.0) == text
    chart.clear()
    assert chart.inspect_at_time(1.0) == ""
    chart.deleteLater()


def test_mini_chart_event_markers_render_and_clear(qapp, snapshot) -> None:
    """Маркеры событий попадают в инспектор и очищаются вместе с историей."""
    chart = MiniChart(30)
    chart.set_series(("temp",))
    chart.push(1.0, snapshot)
    chart.add_event_marker(1.5, "Limit: Power", "limiter")
    chart.add_event_marker(2.0, "WATCHDOG: frozen telemetry", "watchdog")
    chart.add_event_marker(2.0, "gpuFrequencyOffset: 100 -> 150", "setting")
    chart.push(2.0, replace(snapshot, gpu_temp_c=61.0))

    assert len(chart._event_series) == 3
    inspected = chart.inspect_at_time(2.0)
    assert "Limit: Power" in inspected
    assert "WATCHDOG: frozen telemetry" in inspected
    assert "gpuFrequencyOffset: 100 -> 150" in inspected
    with pytest.raises(ValueError, match="неизвестный тип события"):
        chart.add_event_marker(2.0, "Unknown", "other")
    with pytest.raises(ValueError, match="не должна быть пустой"):
        chart.add_event_marker(2.0, " ", "watchdog")

    chart.clear()
    assert not chart._events
    assert not chart._event_series
    chart.deleteLater()


# --- настройки ---


def test_overlay_settings_roundtrip(settings, qapp) -> None:
    """Сохранение/чтение выбора; мусор в настройках схлопывается к дефолтам."""
    assert overlay.load_overlay_enabled() is False  # по умолчанию выключен
    overlay.save_overlay_enabled(True)
    assert overlay.load_overlay_enabled() is True
    settings._store["overlay/enabled"] = "false"  # ini хранит строки
    assert overlay.load_overlay_enabled() is False

    assert overlay.load_overlay_layout() == overlay.DEFAULT_LAYOUT
    overlay.save_overlay_layout("grid")
    assert overlay.load_overlay_layout() == "grid"
    settings._store["overlay/layout"] = "белиберда"
    assert overlay.load_overlay_layout() == overlay.DEFAULT_LAYOUT
    assert overlay.load_overlay_orientation() == "horizontal"
    overlay.save_overlay_orientation("vertical")
    assert overlay.load_overlay_orientation() == "vertical"
    settings._store["overlay/orientation"] = "diagonal"
    assert overlay.load_overlay_orientation() == "horizontal"

    assert overlay.load_overlay_opacity() == overlay.DEFAULT_OPACITY
    overlay.save_overlay_opacity(0.5)
    assert overlay.load_overlay_opacity() == 0.5
    settings._store["overlay/opacity"] = "не число"
    assert overlay.load_overlay_opacity() == overlay.DEFAULT_OPACITY

    with pytest.raises(ValueError, match=r"неизвестный макет"):
        overlay.save_overlay_layout("nope")
    with pytest.raises(ValueError, match=r"непрозрачность"):
        overlay.save_overlay_opacity(0.6)
    with pytest.raises(ValueError, match="неизвестная ориентация"):
        overlay.save_overlay_orientation("diagonal")


def test_overlay_cells_settings(settings, qapp) -> None:
    """Набор метрик: сохранение/чтение; мусор → None (выбирается пресет макета)."""
    assert overlay.load_overlay_cells() is None  # настройки ещё нет
    overlay.save_overlay_cells(["vram", "temp"])
    assert overlay.load_overlay_cells() == ("temp", "vram")
    settings._store["overlay/cells"] = "temp,белиберда"  # мусор не роняет старт
    assert overlay.load_overlay_cells() is None
    settings._store["overlay/cells"] = ""  # пусто — тоже не набор
    assert overlay.load_overlay_cells() is None

    with pytest.raises(ValueError, match=r"хотя бы одна"):
        overlay.save_overlay_cells([])
    with pytest.raises(ValueError, match=r"неизвестные метрики"):
        overlay.save_overlay_cells(["nope"])


def test_mini_chart_settings_validate_and_persist(settings, qapp) -> None:
    """Настройки мини-графика сохраняются; некорректные значения безопасны."""
    assert overlay.load_mini_chart_enabled() is False
    assert overlay.load_mini_chart_series() == DEFAULT_MINI_CHART_SERIES
    assert overlay.load_mini_chart_window() == DEFAULT_MINI_CHART_WINDOW
    assert overlay.load_mini_chart_size() == "compact"

    overlay.save_mini_chart_enabled(True)
    overlay.save_mini_chart_series(["freq", "temp"])
    overlay.save_mini_chart_window(120)
    overlay.save_mini_chart_size("wide")
    assert overlay.load_mini_chart_enabled() is True
    assert overlay.load_mini_chart_series() == ("temp", "freq")
    assert overlay.load_mini_chart_window() == 120
    assert overlay.load_mini_chart_size() == "wide"

    settings._store["overlay/chart_series"] = "temp,unknown"
    settings._store["overlay/chart_window"] = "invalid"
    settings._store["overlay/chart_size"] = "invalid"
    assert overlay.load_mini_chart_series() == DEFAULT_MINI_CHART_SERIES
    assert overlay.load_mini_chart_window() == DEFAULT_MINI_CHART_WINDOW
    assert overlay.load_mini_chart_size() == "compact"

    with pytest.raises(ValueError, match="хотя бы одну"):
        overlay.save_mini_chart_series([])
    with pytest.raises(ValueError, match="неизвестные серии"):
        overlay.save_mini_chart_series(["invalid"])
    with pytest.raises(ValueError, match="временное окно"):
        overlay.save_mini_chart_window(45)
    with pytest.raises(ValueError, match="размер мини-графика"):
        overlay.save_mini_chart_size("huge")


def test_overlay_mini_chart_enable_pause_and_clear(settings, qapp, snapshot) -> None:
    """HUD рисует график только по включению, а пауза/очистка не затрагивают опрос."""
    win = OverlayWindow()
    win.show()
    win.on_telemetry(snapshot)
    assert not win._mini_chart._samples

    win.set_mini_chart_enabled(True)
    assert win._mini_chart.isVisible()
    assert settings._store["overlay/chart_enabled"] is True
    win.set_mini_chart_series(("temp", "freq"))
    win.set_mini_chart_window(30)
    win.on_telemetry(snapshot)
    assert len(win._mini_chart._samples) == 1
    assert win._mini_chart.selected_series == ("temp", "freq")
    assert win._mini_chart.window_s == 30
    win.set_mini_chart_size("wide")
    assert win.mini_chart_size() == "wide"
    assert settings._store["overlay/chart_size"] == "wide"
    assert win._mini_chart.width() > 400

    win.set_mini_chart_paused(True)
    win.on_telemetry(replace(snapshot, gpu_temp_c=70.0))
    assert len(win._mini_chart._samples) == 1
    win.set_mini_chart_paused(False)
    win.on_telemetry(replace(snapshot, gpu_temp_c=71.0))
    assert len(win._mini_chart._samples) == 2

    win.clear_mini_chart()
    assert not win._mini_chart._samples
    win.set_mini_chart_enabled(False)
    assert not win._mini_chart.isVisible()
    win.deleteLater()


def test_overlay_marks_watchdog_and_new_limiter_events(settings, qapp, snapshot) -> None:
    """HUD аннотирует watchdog и переход лимитера во включённое состояние."""
    win = OverlayWindow()
    win.show()
    win.set_mini_chart_enabled(True)
    win.on_telemetry(replace(snapshot, voltage_limited=False))
    win.on_telemetry(replace(snapshot, voltage_limited=True))
    win.on_watchdog_event("TDR", "driver reset")

    win.on_setting_applied("powerLimit: 100 -> 110")
    kinds = [event[2] for event in win._mini_chart._events]
    assert kinds == ["limiter", "watchdog", "setting"]
    assert "Напряжение" in win._mini_chart._events[0][1]
    assert "TDR: driver reset" in win._mini_chart._events[1][1]
    assert "powerLimit: 100 -> 110" in win._mini_chart._events[2][1]
    win.deleteLater()


def test_overlay_hide_emits_visibility(settings, qapp) -> None:
    """Показ/скрытие (вкладка, меню, правый клик) сообщает наверх одним сигналом."""
    win = OverlayWindow()
    win.show()
    fired: list[bool] = []
    win.visibility_changed.connect(fired.append)

    win.set_shown(False)
    assert not win.isVisible()
    assert fired == [False]
    assert overlay.load_overlay_enabled() is False  # выбор переживает перезапуск

    win.set_shown(True)
    assert win.isVisible()
    assert fired == [False, True]
    assert overlay.load_overlay_enabled() is True
    win.deleteLater()


def test_overlay_is_read_only(settings, qapp) -> None:
    """Инвариант проекта: HUD без кнопок и без каналов записи."""
    win = OverlayWindow()
    assert not win.findChildren(QPushButton)
    assert not hasattr(win, "apply_requested")
    assert not hasattr(win, "write_done")
    win.deleteLater()


def test_overlay_label_texts_present(settings, qapp) -> None:
    """Подписи ячеек читаемы и не пусты — в оверлее важна плотность информации."""
    win = OverlayWindow()
    titles = [cell.findChild(QLabel, "overlayTitle").text() for cell in win._cells.values()]
    assert all(titles)
    assert "Температура" in titles
    assert "Загрузка" in titles
    assert "Память занято" in titles
    win.deleteLater()


def test_overlay_displays_optional_cpu_cells(settings, qapp) -> None:
    """CPU-ячейки показывают только реально измеренные загрузку и частоту."""
    win = OverlayWindow()
    win.show()
    win.set_visible_cells(("cpu_load", "cpu_freq", "cpu_temp"))
    win.on_cpu(CpuSnapshot(37.5, (25.0, 50.0), 2400.0, 81.5))

    assert win._cells["cpu_load"].text() == "38 %"
    assert win._cells["cpu_load"]._value.toolTip() == "По ядрам: 25 %, 50 %"
    assert win._cells["cpu_freq"].text() == "2400 МГц"
    assert win._cells["cpu_temp"].text() == "82 °C"

    win.on_cpu(CpuSnapshot(None, (), None))
    assert win._cells["cpu_load"].text() == "—"
    assert win._cells["cpu_freq"].text() == "—"
    assert win._cells["cpu_temp"].text() == "—"
    win.deleteLater()


# --- связка с главным окном ---


def test_main_window_overlay_menu_stays_in_sync(settings, qapp, monkeypatch) -> None:
    """Меню «Вид» и контекстное меню оверлея работают с одними настройками."""
    from arctuner.gui.window import MainWindow

    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)
    win = MainWindow()

    assert win._overlay_toggle.isChecked() is False  # по умолчанию выключен

    # смена из контекстного меню оверлея → галочки в «Вид»
    win._overlay.set_layout("detailed")
    assert win._overlay_layout_actions["detailed"].isChecked()
    assert not win._overlay_layout_actions["compact"].isChecked()

    win._overlay.set_opacity(0.5)
    assert win._overlay_opacity_actions[0.5].isChecked()
    assert not win._overlay_opacity_actions[0.85].isChecked()

    # включение из меню → окно показано и выбор сохранён
    win._overlay_toggle.trigger()
    assert win._overlay.isVisible()
    assert overlay.load_overlay_enabled() is True

    # спрятано своим контекстным меню → галочка снята и сохранена
    win._overlay.set_shown(False)
    assert not win._overlay_toggle.isChecked()
    assert overlay.load_overlay_enabled() is False

    _close_window(win)


def test_main_window_forwards_telemetry_to_overlay(settings, qapp, monkeypatch, snapshot) -> None:
    """Снимки воркера (обычный тик и снимок поиска) доходят до ячеек HUD."""
    from arctuner.gui.window import MainWindow

    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)
    win = MainWindow()
    win._overlay.show()

    # тот же поток → соединение прямое, event loop не нужен
    win._worker.telemetry_ready.emit(snapshot)
    assert win._overlay._cells["temp"].text() == "55 °C"

    win._worker.autotune_sample.emit(replace(snapshot, gpu_temp_c=61.0))
    assert win._overlay._cells["temp"].text() == "61 °C"

    win._monitoring._cpu_load_chart.setChecked(True)
    win._monitoring._cpu_temp_chart.setChecked(True)
    win._worker.cpu_ready.emit(CpuSnapshot(40.0, (30.0, 50.0), 2400.0, 78.0))
    assert win._monitoring._chart._cpu_load_series.count() == 1
    assert win._monitoring._chart._cpu_temp_series.count() == 1
    assert win._overlay._cells["cpu_load"].text() == "40 %"

    win._overlay.set_mini_chart_enabled(True)
    win._worker.watchdog_event.emit("TDR", "driver reset")
    assert win._overlay._mini_chart._events[-1][1] == "TDR: driver reset"
    win._worker.setting_applied.emit("gpuFrequencyOffset: 100 -> 150")
    assert win._overlay._mini_chart._events[-1][1] == "gpuFrequencyOffset: 100 -> 150"

    _close_window(win)


# --- вкладка «Оверлей» ---


def test_overlay_tab_drives_overlay(settings, qapp) -> None:
    """Вкладка — полноценный пульт оверлея; сигналы HUD обратно двигают виджеты."""
    from arctuner.gui.tabs.overlay import OverlayTab

    win = OverlayWindow()
    tab = OverlayTab(win)

    # показ
    assert not tab._enable.isChecked()  # по умолчанию оверлей выключен
    tab._enable.setChecked(True)
    assert win.isVisible()
    assert overlay.load_overlay_enabled() is True

    # макет
    tab._layout_buttons["grid"].click()
    assert win.layout_name() == "grid"
    assert tab._layout_buttons["grid"].isChecked()
    tab._orientation.setCurrentIndex(tab._orientation.findData("vertical"))
    assert win.orientation() == "vertical"
    win.set_orientation("horizontal")
    assert tab._orientation.currentData() == "horizontal"

    # непрозрачность: слайдер ходит только по допустимым шагам
    tab._opacity_slider.setValue(1)
    assert win.current_opacity() == OPACITIES[1]
    assert tab._opacity_label.text() == f"{OPACITIES[1] * 100:g} %"

    # состав метрик
    tab._cell_boxes["sa_vr"].setChecked(True)
    assert "sa_vr" in win.visible_cells()

    # сброс позиции не ломает настройки
    tab._reset_position.click()
    assert win.layout_name() == "grid"
    assert "sa_vr" in win.visible_cells()

    # обратная связь: правый клик/меню HUD двигают вкладку
    win.set_opacity(0.7)
    assert tab._opacity_slider.value() == OPACITIES.index(0.7)
    win.set_layout("detailed")
    assert tab._layout_buttons["detailed"].isChecked()
    assert tab._cell_boxes["sa_vr"].isChecked()  # в подробном — все метрики
    win.set_visible_cells(("temp", "vram"))
    assert tab._cell_boxes["temp"].isChecked()
    assert not tab._cell_boxes["power"].isChecked()

    tab._chart_enable.setChecked(True)
    assert win.mini_chart_enabled()
    assert tab._chart_series_boxes["temp"].isChecked()
    tab._chart_window.setCurrentIndex(tab._chart_window.findData(30))
    assert win.mini_chart_window() == 30
    tab._chart_size.setCurrentIndex(tab._chart_size.findData("wide"))
    assert win.mini_chart_size() == "wide"
    tab._chart_series_boxes["freq"].setChecked(False)
    assert "freq" not in win.mini_chart_series()

    win.set_mini_chart_series(("load", "freq"))
    assert tab._chart_series_boxes["load"].isChecked()
    assert tab._chart_series_boxes["freq"].isChecked()
    win.set_mini_chart_enabled(False)
    assert not tab._chart_enable.isChecked()
    win.set_mini_chart_size("compact")
    assert tab._chart_size.currentData() == "compact"
    tab._orientation.setCurrentIndex(tab._orientation.findData("vertical"))
    assert win.orientation() == "vertical"
    win.set_shown(False)
    assert not tab._enable.isChecked()

    win.deleteLater()


def test_overlay_tab_keeps_at_least_one_cell(settings, qapp) -> None:
    """Пустой HUD запрещён: последнюю снимаемую галочку вкладка возвращает сама."""
    from arctuner.gui.tabs.overlay import OverlayTab

    win = OverlayWindow()
    tab = OverlayTab(win)

    for box in tab._cell_boxes.values():
        box.setChecked(False)

    assert win.visible_cells() == ("vram_used",)  # осталась последняя галочка
    assert tab._cell_boxes["vram_used"].isChecked()
    win.deleteLater()


def test_main_window_overlay_tab_stays_in_sync(settings, qapp, monkeypatch) -> None:
    """Вкладка «Оверлей» и меню «Вид» — две двери в одни настройки оверлея."""
    from arctuner.gui.tabs.overlay import OverlayTab
    from arctuner.gui.window import MainWindow

    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)
    win = MainWindow()

    assert isinstance(win._overlay_tab, OverlayTab)
    # Позиция не захардкожена: добавление вкладок не ломает тест.
    assert win._tabs.tabText(win._tabs.indexOf(win._overlay_tab)) == "Оверлей"

    # вкладка включила оверлей → меню «Вид» подхватило
    win._overlay_tab._enable.setChecked(True)
    assert win._overlay.isVisible()
    assert win._overlay_toggle.isChecked()
    assert overlay.load_overlay_enabled() is True

    # меню выключило → вкладка согласилась
    win._overlay_toggle.trigger()
    assert not win._overlay.isVisible()
    assert not win._overlay_tab._enable.isChecked()
    assert overlay.load_overlay_enabled() is False

    # HUD спрятан своим контекстным меню → обе галочки сняты
    win._overlay_tab._enable.setChecked(True)
    win._overlay.set_shown(False)
    assert not win._overlay_toggle.isChecked()
    assert not win._overlay_tab._enable.isChecked()

    # состав метрик из вкладки дошёл до HUD, а пресет макета вернул его обратно
    win._overlay_tab._cell_boxes["sa_vr"].setChecked(True)
    assert "sa_vr" in win._overlay.visible_cells()
    win._overlay.set_layout("detailed")
    assert win._overlay_tab._cell_boxes["sa_vr"].isChecked()
    assert win._overlay_layout_actions["detailed"].isChecked()

    _close_window(win)
