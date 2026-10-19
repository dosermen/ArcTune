"""Рабочий поток GUI: все вызовы IGCL выполняются здесь, не в UI-потоке.

Handle устройства не потокобезопасен, поэтому весь доступ к карте сериализован
в одном QThread. UI шлёт запросы через queued-слоты и получает результаты
сигналами — окно не подвисает, даже если драйвер отвечает медленно.

Пишет только через GpuContext -> tuner.py (двухуровневые проверки) + журнал.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal, Slot

from arctuner.gui.context import GpuContext
from arctuner.igcl.api import IgclError
from arctuner.replay import IncidentReplay
from arctuner.tuner import SafetyLimitError, VfPoint, WriteReport


class GpuWorker(QObject):
    """Владеет GpuContext и выполняет все операции с картой в своём потоке."""

    device_ready = Signal(object)  # DeviceInfo
    telemetry_ready = Signal(object)  # TelemetrySnapshot
    oc_ready = Signal(object, object)  # ranges: dict, currents: dict
    fans_ready = Signal(object)  # list[FanInfo]
    vf_ready = Signal(object)  # list[tuple[int, int]] (мВ, МГц)
    log_ready = Signal(object)  # list[LogEntry]
    write_done = Signal(str)  # человекочитаемый итог записи
    error = Signal(str)  # ошибка чтения/старта для показа в UI (без диалога)
    write_error = Signal(str)  # ошибка ЗАПИСИ — показывается модально
    busy_changed = Signal(bool)
    incident_replay = Signal(str)  # записан дамп реплея инцидента (путь к JSONL)
    autotune_progress = Signal(int, int, str)  # current, total, text
    autotune_completed = Signal(object)  # TuningResult

    #: имя пункта UI -> метод GpuContext (все проходят tuner-проверки)
    _WRITERS: ClassVar[dict[str, Callable[..., WriteReport]]] = {
        "powerLimit": GpuContext.set_power_limit,
        "gpuFrequencyOffset": GpuContext.set_freq_offset,
        "gpuVoltageOffset": GpuContext.set_voltage_offset,
        "temperatureLimit": GpuContext.set_temp_limit,
        "vramMemSpeedLimit": GpuContext.set_vram_speed,
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ctx = GpuContext()
        # Реплей инцидента: буфер создаётся после открытия сессии (нужно имя
        # устройства). Read-only: ни один звонок реплея не меняет карту.
        self._replay: IncidentReplay | None = None

    # --- жизненный цикл ---

    @Slot()
    def start(self) -> None:
        """Открывает IGCL-сессию и ищет дискретную Arc."""
        try:
            info = self._ctx.open()
        except (IgclError, SafetyLimitError, OSError) as e:
            self.error.emit(str(e))
            return
        self._replay = IncidentReplay(device=info.name)
        self.device_ready.emit(info)
        self.reload_static()
        self.poll_telemetry()

    @Slot()
    def shutdown(self) -> None:
        """Закрывает сессию. Должен вызываться в потоке воркера (queued)."""
        self._ctx.close()

    def attach_thread(self, thread: QThread) -> None:
        """Переносит воркер в поток. Вызывать ДО thread.start()."""
        self.moveToThread(thread)
        thread.started.connect(self.start)

    # --- чтение ---

    @Slot()
    def poll_telemetry(self) -> None:
        if not self._ctx.is_open:
            # Сессия не открыта: ошибка старта уже показана один раз. Молча
            # пропускаем тик, чтобы не спамить диалогом каждую секунду.
            return
        try:
            snap = self._ctx.telemetry()
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(f"телеметрия: {e}")
            return
        self.telemetry_ready.emit(snap)
        self._replay_telemetry(snap)

    def _replay_telemetry(self, snap: object) -> None:
        """Толкает снимок в буфер реплея; при инциденте пишет дамп и извещает UI.

        Сбой дампа не останавливает опрос телеметрии (error-сигнал, без диалога).
        """
        if self._replay is None:
            return
        try:
            trigger = self._replay.push(snap)
            if not trigger:
                return
            path = self._replay.dump(trigger)
            if path is not None:
                self.incident_replay.emit(str(path))
        except OSError as e:
            self.error.emit(f"реплей инцидента: {e}")

    @Slot()
    def reload_static(self) -> None:
        """Перечитывает диапазоны OC, текущие значения, вентиляторы, V/F и журнал.

        Блоки независимы: сбой одного не должен оставлять пустыми остальные
        вкладки (раньше единый try/except обнулял всё при первой ошибке).
        """
        try:
            self.oc_ready.emit(self._ctx.oc_ranges(), self._ctx.current_values())
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(f"диапазоны OC: {e}")
        try:
            self.fans_ready.emit(self._ctx.fans())
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(f"вентиляторы: {e}")
        try:
            self.vf_ready.emit(self._ctx.vf_curve())
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(f"кривая V/F: {e}")
        self.log_ready.emit(self._ctx.log.read_all())

    @Slot()
    def reload_fans(self) -> None:
        try:
            self.fans_ready.emit(self._ctx.fans())
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(str(e))

    # --- запись (⚠️ меняет состояние карты) ---

    @Slot(str, float)
    def apply(self, field: str, value: float) -> None:
        """Ставит одно значение. Подтверждение уже сделано в UI; здесь — проверки."""
        writer = self._WRITERS.get(field)
        if writer is None:
            self.write_error.emit(f"неизвестное поле: {field}")
            return
        self._guarded(lambda: self._emit_write(writer(self._ctx, value)))

    @Slot()
    def reset_oc(self) -> None:
        self._guarded(lambda: self._emit_write(self._ctx.reset_oc()))

    @Slot(int)
    def set_fan(self, rpm: int) -> None:
        """Фиксирует ВСЕ управляемые вентиляторы на rpm (как CLI fans --set)."""

        def run() -> None:
            applied = 0
            for fan in self._ctx.fans():
                if not fan.props.canControl:
                    continue
                self._ctx.set_fan_speed(fan.index, rpm)
                applied += 1
            self.write_done.emit(f"вентиляторы: {applied} шт. зафиксированы на {rpm} RPM")
            # Перечитываем RPM: иначе вкладка покажет старую скорость до окна опроса.
            self.reload_fans()

        self._guarded(run)

    @Slot()
    def reset_fans(self) -> None:
        def run() -> None:
            self._ctx.reset_fans()
            self.write_done.emit("вентиляторы возвращены в авто")
            self.reload_fans()

        self._guarded(run)

    @Slot()
    def rollback(self) -> None:
        """Возвращает настройки к исходным значениям журнала."""

        def run() -> None:
            done = self._ctx.rollback()
            if not done:
                self.write_done.emit("нечего откатывать — журнал пуст")
            else:
                summary = ", ".join(f"{name}={value:g}" for name, value in done)
                self.write_done.emit(f"откат: {summary}")
            self.reload_static()

        self._guarded(run)

    # --- кривая V/F ---

    @Slot(str)
    def export_vf(self, path: str) -> None:
        """Пишет текущую кривую V/F в CSV (тот же формат, что CLI vf --csv)."""

        def run() -> None:
            points = self._ctx.vf_curve()
            out = Path(path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["voltage_mv", "freq_mhz"])
                w.writerows(points)
            self.write_done.emit(f"V/F экспортирован: {out} ({len(points)} точек)")

        self._guarded(run)

    @Slot(object)
    def apply_vf(self, points: list[VfPoint]) -> None:
        """Пишет кривую V/F. Валидация форма/границы — в tuner.apply_vf_curve."""
        self._guarded(lambda: self._emit_write(self._ctx.apply_vf_curve(points)))

    # --- авторазгон ---

    @Slot(object)
    def autotune_search(self, config: object) -> None:
        """Запускает адаптивный поиск авторазгона.

        Выполняется в рабочем потоке. На каждом шаге — прогресс-сигнал.
        В конце — итоговый TuningResult через сигнал.
        """
        from arctuner.autotune.engine import TuningEngine

        def run() -> None:
            engine = TuningEngine(self._ctx.api, self._ctx.handle, config)

            def _on_step(step: int, value: float, snap: object) -> None:
                self.autotune_progress.emit(step, step, f"Шаг {step}: {value:.2f}")

            result = engine.run(on_step=_on_step)
            self.autotune_completed.emit(result)
            self.reload_static()

        self._guarded(run)

    # --- внутренности ---

    def _guarded(self, fn: Callable[[], None]) -> None:
        """Выполняет операцию записи.

        Ошибки уходят в write_error (модальный диалог — запись не должна пройти
        молча), занятость — в busy_changed (UI блокирует кнопки записей).
        """
        self.busy_changed.emit(True)
        try:
            fn()
        except SafetyLimitError as e:
            self.write_error.emit(f"заблокировано проверкой: {e}")
        except IgclError as e:
            self.write_error.emit(f"ошибка драйвера: {e}")
        except OSError as e:
            self.write_error.emit(f"файл/диск: {e}")
        finally:
            self.busy_changed.emit(False)

    def _emit_write(self, report: WriteReport) -> None:
        """Показывает итог записи и перечитывает статику, чтобы UI был свежим."""
        if report.new is None:
            line = report.field
        else:
            line = f"{report.field}: {report.old:g} -> {report.new:g}"
        if report.note:
            line += f" ({report.note})"
        self.write_done.emit(line)
        self.reload_static()


__all__ = ["GpuWorker"]
