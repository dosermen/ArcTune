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
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from arctuner.gui import overlay  # noqa: E402
from arctuner.gui.overlay import LAYOUTS, OPACITIES, OverlayWindow  # noqa: E402
from arctuner.igcl.api import TelemetrySnapshot  # noqa: E402


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
    """Без настроек — полоса 1×4 и 85 %; окно само по себе не показывается."""
    win = OverlayWindow()
    assert win.layout_name() in LAYOUTS
    assert win.layout_name() == overlay.DEFAULT_LAYOUT
    assert win.current_opacity() in OPACITIES
    assert win.current_opacity() == overlay.DEFAULT_OPACITY
    assert not win.isVisible()  # пользователь решает, показывать ли HUD
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
    """Пресет макета возвращает свой набор метрик — подписи «1×4» и т.п. не врут."""
    win = OverlayWindow()
    win.set_visible_cells(["temp"])

    win.set_layout("grid")
    assert win.visible_cells() == ("temp", "power", "freq", "vram")
    assert settings._store["overlay/cells"] == "temp,power,freq,vram"
    assert not win._cells["temp"].isHidden()
    assert not win._cells["vram"].isHidden()  # (isHidden, т.к. окно не показано)
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

    assert overlay.load_overlay_opacity() == overlay.DEFAULT_OPACITY
    overlay.save_overlay_opacity(0.5)
    assert overlay.load_overlay_opacity() == 0.5
    settings._store["overlay/opacity"] = "не число"
    assert overlay.load_overlay_opacity() == overlay.DEFAULT_OPACITY

    with pytest.raises(ValueError, match=r"неизвестный макет"):
        overlay.save_overlay_layout("nope")
    with pytest.raises(ValueError, match=r"непрозрачность"):
        overlay.save_overlay_opacity(0.6)


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

    assert win.visible_cells() == ("vram",)  # осталась последняя галочка
    assert tab._cell_boxes["vram"].isChecked()
    win.deleteLater()


def test_main_window_overlay_tab_stays_in_sync(settings, qapp, monkeypatch) -> None:
    """Вкладка «Оверлей» и меню «Вид» — две двери в одни настройки оверлея."""
    from arctuner.gui.tabs.overlay import OverlayTab
    from arctuner.gui.window import MainWindow

    monkeypatch.setattr("arctuner.gui.window.QMessageBox.warning", lambda *a, **k: None)
    win = MainWindow()

    assert isinstance(win._overlay_tab, OverlayTab)
    assert win._tabs.tabText(6) == "Оверлей"

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
