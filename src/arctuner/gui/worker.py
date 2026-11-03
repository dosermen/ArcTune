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
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtCore import QObject, QThread, Signal, Slot

from arctuner.gui.context import DeviceReport, GpuContext
from arctuner.igcl.api import IgclError
from arctuner.replay import IncidentReplay
from arctuner.telemetry import export_telemetry, snapshot_csv_row
from arctuner.tuner import SafetyLimitError, VfPoint, WriteReport

if TYPE_CHECKING:
    from arctuner.autotune.engine import TuningConfig
    from arctuner.igcl.api import TelemetrySnapshot


class GpuWorker(QObject):
    """Владеет GpuContext и выполняет все операции с картой в своём потоке."""

    device_ready = Signal(object)  # DeviceInfo
    device_info_ready = Signal(object, object)  # (DeviceInfo, ExtendedDeviceInfo)
    device_report_ready = Signal(object)  # DeviceReport (PCIe/прошивка/ECC)
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
    #: ctlClose выполнен в потоке воркера (или упал — сигнал всё равно уходит).
    #: Окно останавливает QThread только после него.
    shutdown_done = Signal()
    autotune_progress = Signal(int, int, str)  # current, total, text
    autotune_sample = Signal(object)  # TelemetrySnapshot — живой график во время поиска
    autotune_completed = Signal(object)  # TuningResult
    autotune_error = Signal(str)  # ошибка процесса авторазгона (не одиночная запись)
    #: старт поиска: UI сначала зовёт autotune_arm() синхронно, затем эмитит это
    #: (queued) — «Стоп», нажатый до выполнения слота, не должен теряться.
    autotune_start = Signal(object)  # TuningConfig

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
        self._autotune_stop = False  # флаг отмены авторазгона (autotune_stop)
        # Реплей инцидента: буфер создаётся после открытия сессии (нужно имя
        # устройства). Read-only: ни один звонок реплея не меняет карту.
        self._replay: IncidentReplay | None = None
        # self-сигнал → слот в этом же потоке: emit из UI-потока встаёт в очередь
        # воркера и не выполняет поиск в UI-потоке.
        self.autotune_start.connect(self.autotune_search)

    # --- жизненный цикл ---

    @Slot()
    def start(self) -> None:
        """Открывает IGCL-сессию и ищет дискретную Arc."""
        try:
            info = self._ctx.open()
        except (IgclError, SafetyLimitError, OSError, RuntimeError) as e:
            # RuntimeError — IgclNotFoundError (DLL/драйвер не найдены): без этой
            # ветки слот падал молча, и окно навсегда оставалось на «Подключение…».
            self.error.emit(str(e))
            return
        self._replay = IncidentReplay(device=info.name)
        self.device_ready.emit(info)
        self.reload_static()
        self.reload_device()  # статус-бар + вкладка «Об устройстве» сразу при старте
        self.poll_telemetry()

    @Slot()
    def shutdown(self) -> None:
        """Закрывает сессию. Должен вызываться в потоке воркера (queued).

        shutdown_done уходит в любом случае: окно должно уметь остановить
        QThread даже если ctx.close() бросил (иначе поток остался бы живым,
        а окно висело бы на «Завершение…» до убийства процесса). Ошибку
        тоже эмитим, чтобы UI показал её пользователю.
        """
        try:
            self._ctx.close()
        except Exception as exc:
            self.error.emit(f"shutdown ошибка: {exc}")
        self.shutdown_done.emit()

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

    def _replay_telemetry(self, snap: TelemetrySnapshot) -> None:
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
    def reload_device(self) -> None:
        """Читает всё для вкладки «Об устройстве» и статус-бара (read-only).

        info + extended → ``device_info_ready``; PCIe / прошивка / ECC →
        ``device_report_ready`` (DeviceReport). Секции независимы: сбой одной
        (ECC не поддерживается, драйвер не отвечает) попадает в report.errors,
        остальные поля при этом заполняются.
        """
        if not self._ctx.is_open:
            self.error.emit("reload_device: сессия не открыта")
            return
        try:
            info = self._ctx.info
            extended = self._ctx.extended_info  # ExtendedDeviceInfo
            if info is None or extended is None:
                self.error.emit("reload_device: данные не получены")
            else:
                self.device_info_ready.emit(info, extended)
        except (IgclError, SafetyLimitError) as e:
            self.error.emit(f"информация устройства: {e}")

        errors: list[str] = []
        pci = None
        firmware = None
        ecc = None
        try:
            pci = self._ctx.pci_state()
        except (IgclError, SafetyLimitError) as e:
            errors.append(f"PCIe: нет данных ({e})")
        try:
            firmware = self._ctx.firmware_info()
        except (IgclError, SafetyLimitError) as e:
            errors.append(f"прошивка: драйвер не сообщает ({e})")
        try:
            ecc = self._ctx.ecc_state()
        except (IgclError, SafetyLimitError) as e:
            errors.append(f"ECC: не поддерживается ({e})")
        self.device_report_ready.emit(
            DeviceReport(pci=pci, firmware=firmware, ecc=ecc, errors=tuple(errors))
        )

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
        try:
            self.log_ready.emit(self._ctx.log.read_all())
        except OSError as e:
            # Журнал может быть недоступен (права/блокировка/диск). Раньше
            # исключение летело из finally автопоиска мимо busy_changed(False) —
            # кнопки навсегда оставались заблокированными.
            self.error.emit(f"журнал: {e}")

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
        """Фиксирует ВСЕ управляемые вентиляторы на rpm (как CLI fans --set).

        При сбое на N-м вентиляторе уже зафиксированные возвращаются в авто:
        частичная фиксация (часть в фиксе, часть в авто) неравномерно охлаждает
        карту и оставляет пользователя в неведении.
        """

        def run() -> None:
            applied: list[int] = []
            try:
                for fan in self._ctx.fans():
                    if not fan.props.canControl:
                        continue
                    self._ctx.set_fan_speed(fan.index, rpm)
                    applied.append(fan.index)
            except Exception as exc:
                if applied:
                    # Все в авто: reset_fans журналирует маркер — история честная.
                    try:
                        self._ctx.reset_fans()
                    except Exception as rb_exc:
                        self.write_error.emit(
                            f"вентиляторы: {exc}; откат в авто не удался "
                            f"({rb_exc}) — состояние неизвестно, проверьте вкладку"
                        )
                        return
                    self.write_error.emit(f"вентиляторы: {exc} — все возвращены в авто")
                else:
                    self.write_error.emit(f"вентиляторы: {exc}")
                return
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

    @Slot(str)
    def export_telemetry_csv(self, path: str) -> None:
        """Пишет буфер телеметрии (последние ~10 мин наблюдения) в CSV.

        Формат общий с CLI ``telemetry --csv`` (export_telemetry из telemetry.py):
        буфер копируется без очистки, инцидентные дампы не затрагиваются.
        """

        def run() -> None:
            if self._replay is None or not self._replay.samples:
                # Не ошибка записи, а пустой буфер: сообщение без модального диалога.
                self.error.emit("нет данных для экспорта — сначала понаблюдайте за телеметрией")
                return
            rows = [snapshot_csv_row(snap, wall) for wall, snap in self._replay.iter_samples()]
            out = Path(path)
            written = export_telemetry(out, rows)
            self.write_done.emit(f"телеметрия экспортирована: {out} ({written} строк)")

        self._guarded(run)

    @Slot(object)
    def apply_vf(self, points: list[VfPoint]) -> None:
        """Пишет кривую V/F. Валидация форма/границы — в tuner.apply_vf_curve."""
        self._guarded(lambda: self._emit_write(self._ctx.apply_vf_curve(points)))

    # --- авторазгон ---

    @Slot(object)
    def autotune_search(self, config: TuningConfig) -> None:
        """Запускает адаптивный поиск авторазгона (в этом потоке).

        Пишет через GpuContext (tuner-проверки + журнал). Прогресс —
        autotune_progress(шаг, всего_шагов, текст); итог — autotune_completed.
        Отмена — через autotune_stop(): движок откатит к стабильному значению.

        Флаг отмены здесь НЕ сбрасывается: слот может стоять в очереди, пока
        пользователь уже нажал «Стоп». Сброс — только autotune_arm() из UI
        перед стартом (прямой вызов, до постановки слота в очередь).
        """
        from arctuner.autotune.engine import TuningEngine
        from arctuner.autotune.watchdog import Watchdog

        def run() -> None:
            ctx = self._ctx
            watchdog = Watchdog(device=ctx.info.name if ctx.info else "")
            engine = TuningEngine(ctx, config, watchdog=watchdog)
            total = getattr(config, "max_steps", 0) or 0

            def _on_step(step: int, value: float, snap: object) -> None:
                # Значение УЖЕ применено и проверено — называем его стабильным,
                # а не «кандидатом» (иначе непонятно, применилось ли вообще).
                self.autotune_progress.emit(step, total, f"{value:+g} применён, стабилен")

            def _on_status(text: str) -> None:
                # current=0: вкладка покажет статус без «шага» (этап перед шагом).
                self.autotune_progress.emit(0, total, text)

            result = engine.run(
                on_step=_on_step,
                stop_check=lambda: self._autotune_stop,
                on_status=_on_status,
                on_sample=self._autotune_sample,
            )
            self.autotune_completed.emit(result)

        self.busy_changed.emit(True)
        try:
            run()
        except SafetyLimitError as e:
            self.autotune_error.emit(f"заблокировано проверкой: {e}")
        except (IgclError, OSError) as e:
            self.autotune_error.emit(f"ошибка авторазгона: {e}")
        except Exception as e:
            # Всё «иное» (TypeError из битого профиля, сбой колбэка, …) обязано
            # дойти до пользователя каналом ошибок, а не в невидимый stderr exe.
            self.autotune_error.emit(f"неожиданная ошибка поиска: {e!r}")
        finally:
            self.reload_static()  # карта могла измениться (шаг/откат) — обновить статику
            self.busy_changed.emit(False)

    def _autotune_sample(self, snap: TelemetrySnapshot) -> None:
        """Снимок движка (в его потоке): живой график + реплей инцидентов.

        Вызывается на каждое чтение телеметрии поиском (включая settle), а не
        по таймеру: опрос в UI-потоке во время поиска остановлен, и только так
        график получает данные без второго конкурентного доступа к IGCL.
        """
        self.autotune_sample.emit(snap)
        self._replay_telemetry(snap)

    def autotune_arm(self) -> None:
        """Сбрасывает флаг отмены перед стартом поиска (прямой вызов из UI).

        Вызывается ДО постановки autotune_search в очередь: тогда «Стоп»,
        нажатый между кликом и выполнением слота, остаётся действующим.
        """
        self._autotune_stop = False

    def autotune_stop(self) -> None:
        """Ставит флаг отмены; движок проверит его перед следующим шагом.

        Вызывается НАПРЯМУЯЙ из UI-потока (не через queued-слот): воркер-поток
        занят синхронным поиском и очередь слотов не обрабатывает — queued-стоп
        сработал бы только ПОСЛЕ завершения поиска. Присваивание bool
        потокобезопасно под GIL.
        """
        self._autotune_stop = True

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
        except Exception as e:
            self.write_error.emit(f"неожиданная ошибка записи: {e!r}")
        finally:
            self.busy_changed.emit(False)

    def _emit_write(self, report: WriteReport) -> None:
        """Показывает итог записи и перечитывает статику, чтобы UI был свежим."""
        if report.new is None:
            line = report.field
        else:
            # old может быть None (значение до записи не прочиталось) — не «?»,
            # а честное «неизвестно»; f-string на None раньше падал TypeError.
            old = f"{report.old:g}" if report.old is not None else "неизвестно"
            line = f"{report.field}: {old} -> {report.new:g}"
        if report.note:
            line += f" ({report.note})"
        self.write_done.emit(line)
        self.reload_static()


__all__ = ["GpuWorker"]
