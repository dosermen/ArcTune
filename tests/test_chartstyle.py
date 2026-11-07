"""Стиль и фон графиков: пресеты, glow-подложки, пульсация, меню «Вид».

Проверяем визуальные пресеты (classic/dark/neon/pulse) на всех трёх графиках
приложения и связку «меню → настройки → применение ко всем графикам».
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from arctuner import i18n
from arctuner.gui import chartstyle
from arctuner.gui.chartstyle import CHART_STYLES, chart_style
from arctuner.gui.theme import SERIES_FREQ, SERIES_TEMP
from arctuner.igcl.api import TelemetrySnapshot


class _FakeSettings:
    """Подмена QSettings: значения хранятся в словаре процесса."""

    _store: ClassVar[dict[str, object]] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def value(self, key: str, default: object = None) -> object:
        return self._store.get(key, default)

    def setValue(self, key: str, value: object) -> None:  # noqa: N802 (Qt-имя)
        self._store[key] = value


@pytest.fixture()
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> _FakeSettings:
    _FakeSettings._store.clear()
    monkeypatch.setattr(chartstyle, "QSettings", _FakeSettings)
    return _FakeSettings()


def snapshot() -> TelemetrySnapshot:
    """Полный снимок телеметрии для мини-графика."""
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
        gpu_energy_counter_j=1000.0,
        card_energy_counter_j=5000.0,
        vram_energy_counter_j=100.0,
    )


# --- настройки и подписи ---


def test_chart_style_roundtrip(fake_settings: _FakeSettings) -> None:
    """Сохранение/чтение стиля: дефолт — классика, мусор восстанавливается."""
    assert chartstyle.load_chart_style() == chartstyle.DEFAULT_CHART_STYLE
    chartstyle.save_chart_style("neon")
    assert chartstyle.load_chart_style() == "neon"
    fake_settings._store["ui/chart_style"] = "белиберда"
    assert chartstyle.load_chart_style() == chartstyle.DEFAULT_CHART_STYLE
    fake_settings._store["ui/chart_style"] = "pulse"
    assert chartstyle.load_chart_style() == "pulse"


def test_chart_style_rejects_unknown() -> None:
    """Неизвестное имя стиля — ValueError везде, в настройки мусор не пишется."""
    for call in (
        lambda: chartstyle.chart_style("midnight"),
        lambda: chartstyle.save_chart_style("midnight"),
        lambda: chartstyle.chart_style_label("midnight"),
    ):
        with pytest.raises(ValueError, match="неизвестный стиль графиков"):
            call()


def test_chart_style_labels_follow_language() -> None:
    """Подписи пунктов меню на текущем языке (ru по умолчанию, en — по выбору)."""
    assert chartstyle.chart_style_label("classic") == "Классика"
    assert chartstyle.chart_style_label("neon") == "Неон"
    i18n.set_language(i18n.LANG_EN)
    try:
        assert chartstyle.chart_style_label("classic") == "Classic"
        assert chartstyle.chart_style_label("pulse") == "Pulse"
    finally:
        i18n.set_language(i18n.LANG_RU)


# --- LiveChart ---


def test_live_chart_neon_style(qapp) -> None:
    """Неон: тёмные кисти, широкие линии, glow-подложки под всеми сериями."""
    from PySide6.QtCharts import QChart

    from arctuner.gui.charts import LiveChart

    live = LiveChart(window_s=60)
    live.set_chart_style("neon")
    spec = chart_style("neon")
    chart = live.chart()
    assert chart.plotAreaBackgroundBrush().color().name() == spec.plot_bg
    assert chart.backgroundBrush().color().name() == spec.bg
    assert chart.isPlotAreaBackgroundVisible()
    assert chart.axes()[0].labelsBrush().color().name() == spec.labels
    # тема мониторинга не участвует (set_dark — no-op, вид задаёт только стиль)
    assert chart.theme() == QChart.ChartTheme.ChartThemeLight

    assert len(live._glow.tracked) == 5
    assert len(live._glow.twins) == 5
    before = len(live._glow.tracked)
    live._glow.track(live._load_series)  # повторный track — no-op (гард двойной регистрации)
    assert len(live._glow.tracked) == before
    for series in live._glow.tracked:
        assert series.pen().widthF() == spec.line_width
    for twin in live._glow.twins.values():
        pen = twin.pen()
        assert pen.widthF() == spec.line_width * 3  # подложка шире линии
        assert pen.color().alpha() == 60  # и полупрозрачна
        # маркеры подложек не попадают в легенду
        markers = chart.legend().markers(twin)
        assert markers and not any(marker.isVisible() for marker in markers)
    assert not live._pulse.running  # в неоне пульсации нет


def test_live_chart_classic_resets_neon(qapp) -> None:
    """Возврат к классике: светлые кисти, без glow, ширина 2, цвета SERIES_*."""
    from PySide6.QtCharts import QChart

    from arctuner.gui.charts import LiveChart

    live = LiveChart(window_s=60)
    live.set_chart_style("neon")
    live.set_chart_style("classic")
    chart = live.chart()
    assert chart.plotAreaBackgroundBrush().color().name() == "#ffffff"
    assert chart.backgroundBrush().color().name() == "#ffffff"
    assert not live._glow.twins
    for series in live._glow.tracked:
        assert series.pen().widthF() == 2.0
    assert live._temp_series.pen().color().name() == SERIES_TEMP
    assert chart.theme() == QChart.ChartTheme.ChartThemeLight  # тема не менялась
    assert not live._pulse.running


def test_glow_follows_points_and_visibility(qapp) -> None:
    """Подложка повторяет точки и видимость основной серии (сигнала pointsChanged нет)."""
    from arctuner.gui.charts import LiveChart

    live = LiveChart(window_s=60)
    live.set_chart_style("neon")
    live.push(1.0, 55.0, 70.0, 2500.0, 42.0, 30.0, 10.0)
    main = live._load_series
    twin = live._glow.twins[main]
    assert list(twin.points()) == list(main.points())

    main.setVisible(False)
    assert not twin.isVisible()  # visibleChanged → sync
    main.setVisible(True)
    assert twin.isVisible()

    live.clear()
    assert list(twin.points()) == []  # sync_all после очистки


def test_pulse_timer_lifecycle(qapp) -> None:
    """Пульсация: таймер идёт, тик меняет фон, скрытый график не дышит."""
    from PySide6.QtGui import QColor

    from arctuner.gui.charts import LiveChart

    live = LiveChart(window_s=60)
    pulse = live._pulse
    live.set_chart_style("pulse")
    spec = chart_style("pulse")
    assert pulse.active and pulse.running
    assert pulse._timer.interval() == 100

    # фаза в верхней точке: sin(pi/2) = 1 → phase 1 → цвет pulse_hi
    pulse._clock = lambda: 0.5
    live.show()
    qapp.processEvents()
    pulse._tick()
    assert live.chart().plotAreaBackgroundBrush().color().name() == spec.pulse_hi

    # график скрыт — тик ничего не трогает (дыхание только на экране)
    live.hide()
    qapp.processEvents()
    live.chart().setPlotAreaBackgroundBrush(QColor(spec.plot_bg))
    pulse._tick()
    assert live.chart().plotAreaBackgroundBrush().color().name() == spec.plot_bg

    live.set_chart_style("classic")
    assert not pulse.active and not pulse.running


def test_blend_endpoints() -> None:
    """Интерполяция цветов пульсации: 0 — низ, 1 — верх, середина — между."""
    from PySide6.QtGui import QColor

    low, high = QColor("#0a0e18"), QColor("#16233d")
    assert chartstyle._blend(low, high, 0.0) == low
    assert chartstyle._blend(low, high, 1.0) == high
    mid = chartstyle._blend(low, high, 0.5)
    assert (mid.red(), mid.green(), mid.blue()) == (16, 24, 42)  # середина диапазона


# --- VfChart ---


def test_vf_classic_follows_theme(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    """Классика V/F следует теме приложения; неон живёт на тёмной базе."""
    from PySide6.QtCharts import QChart

    from arctuner.gui.charts import VfChart

    monkeypatch.setattr("arctuner.gui.charts.load_theme", lambda: "light")
    vf = VfChart()
    vf.set_curve([(500, 700), (600, 750), (700, 800)])

    vf.set_chart_style("neon")
    spec = chart_style("neon")
    assert vf.chart().plotAreaBackgroundBrush().color().name() == spec.plot_bg
    assert len(vf._glow.twins) == 1  # подложка под линией кривой
    assert vf._scatter.markerSize() > 15.0  # маркеры крупнее на тёмном фоне

    vf.set_chart_style("classic")
    assert vf.chart().theme() == QChart.ChartTheme.ChartThemeLight
    assert not vf._glow.twins
    assert vf._series.pen().color().name() == SERIES_FREQ  # тогл темы не сломал линию
    assert vf._scatter.markerSize() == 15.0

    monkeypatch.setattr("arctuner.gui.charts.load_theme", lambda: "dark")
    vf.set_chart_style("classic")
    assert vf.chart().theme() == QChart.ChartTheme.ChartThemeDark


def test_vf_glow_sync_on_edit(qapp) -> None:
    """Правка точки мышью/кодом доходит до glow-подложки."""
    from arctuner.gui.charts import VfChart

    vf = VfChart()
    vf.set_curve([(500, 700), (600, 750), (700, 800)])
    vf.set_chart_style("neon")
    twin = vf._glow.twins[vf._series]
    assert list(twin.points()) == list(vf._series.points())

    vf.set_editable(True)
    assert vf.edit_point(1, 760, 650)  # (мВ, МГц) внутри клампов
    assert list(twin.points()) == list(vf._series.points())


# --- MiniChart ---


def test_minichart_style(qapp) -> None:
    """Мини-график HUD переключается вместе со всеми: кисти, glow, возврат."""
    from arctuner.gui.minichart import MiniChart

    mini = MiniChart(60)
    mini.push(1.0, snapshot())
    mini.push(2.0, snapshot())

    mini.set_chart_style("neon")
    spec = chart_style("neon")
    assert mini.chart().plotAreaBackgroundBrush().color().name() == spec.plot_bg
    assert mini._glow.tracked
    assert len(mini._glow.twins) == len(mini._glow.tracked)

    mini.set_chart_style("classic")
    assert mini.chart().plotAreaBackgroundBrush().color().name() == "#ffffff"
    assert not mini._glow.twins
    assert not mini._pulse.running
    # канонические цвета метрических серий не пострадали
    for series in mini._glow.tracked:
        assert series.pen().widthF() == 2.0


def test_minichart_glow_drops_removed_segments(qapp) -> None:
    """Слияние сегментов при активном glow не оставляет осиротевших twin."""
    from arctuner.gui.minichart import MiniChart

    mini = MiniChart(60)
    mini.push(1.0, snapshot())
    mini.push(10.0, snapshot())  # гап 9 с > _GAP_MAX_S → второй сегмент у каждой метрики
    mini.set_chart_style("neon")
    two_segments = len(mini._glow.tracked)
    assert two_segments > 0
    assert len(mini._glow.twins) == two_segments

    # повторное применение glow пересоздаёт подложки (смена стиля/темы) —
    # скрытые вручную маркеры легенды доп. сегментов не должны вернуться
    mini.set_chart_style("neon")
    for series in mini._glow.tracked:
        if not series.name():
            markers = mini.chart().legend().markers(series)
            assert markers and not any(marker.isVisible() for marker in markers)

    # гап исчезает: сегменты сливаются, лишние серии удаляются вместе с twin
    mini.clear()
    mini.push(1.0, snapshot())
    assert 0 < len(mini._glow.tracked) < two_segments
    assert len(mini._glow.twins) == len(mini._glow.tracked)
    chart_series = set(mini.chart().series())
    assert all(main in chart_series for main in mini._glow.twins)
    assert all(twin in chart_series for twin in mini._glow.twins.values())


# --- меню «Вид» главного окна ---


def test_window_chart_style_menu(
    qapp, monkeypatch: pytest.MonkeyPatch, fake_settings: _FakeSettings
) -> None:
    """Пункт «Стиль графиков»: 4 пресета, сохранение и применение ко всем графикам."""
    from arctuner.gui.window import MainWindow

    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)
    win = MainWindow()
    win._timer.stop()

    actions = win._chart_style_actions
    assert set(actions) == set(CHART_STYLES)
    assert actions["classic"].isChecked()  # дефолт в чистых настройках
    for name, action in actions.items():
        assert action.text() == chartstyle.chart_style_label(name)

    # у мини-графика пока нет данных — пусть серии появятся до включения неона
    win._overlay._mini_chart.push(1.0, snapshot())

    actions["neon"].trigger()
    assert chartstyle.load_chart_style() == "neon"
    assert actions["neon"].isChecked() and not actions["classic"].isChecked()
    spec = chart_style("neon")
    live = win._monitoring._chart
    mini = win._overlay._mini_chart
    assert live.chart().plotAreaBackgroundBrush().color().name() == spec.plot_bg
    assert mini.chart().plotAreaBackgroundBrush().color().name() == spec.plot_bg
    assert live._glow.twins and mini._glow.twins  # свечение — на обоих

    actions["classic"].trigger()
    assert chartstyle.load_chart_style() == "classic"
    assert live.chart().plotAreaBackgroundBrush().color().name() == "#ffffff"
    assert mini.chart().plotAreaBackgroundBrush().color().name() == "#ffffff"
    assert not live._glow.twins and not mini._glow.twins

    # закрытие как в остальных тестах окна: ждём конца воркера, а не висим
    win.close()
    if win._thread.isRunning():
        from PySide6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        win._thread.finished.connect(loop.quit)
        timer.start(5000)
        loop.exec()
        timer.stop()
    assert not win._thread.isRunning(), "поток воркера не завершился за 5 с"
