"""CLI ArcTune.

Команды:
    doctor      — проверка окружения (драйвер, IGCL, функции)
    telemetry   — снимок телеметрии (read-only)
    status      — диапазоны пунктов разгона (read-only)
    set         — установка настроек (⚠️ меняет состояние карты)

Запись настроек (set) изолирована в tuner.py и проходит проверки диапазонов.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial


def cmd_doctor(_: argparse.Namespace) -> int:
    """Проверяет, видит ли система IGCL API и ключевые функции."""
    from arctuner.igcl.loader import IgclLoader, IgclNotFoundError

    try:
        loader = IgclLoader()
    except IgclNotFoundError as e:
        print(f"[FAIL] {e}")
        return 1

    info = loader.healthcheck()
    print(f"[OK]   IGCL API: {info['path']}")
    missing = info["missing_required"]
    if missing:
        print(f"[FAIL] Отсутствуют обязательные функции (старый драйвер?): {', '.join(missing)}")
        print("Обновите драйвер Intel Arc — без этих ctl* функций работа невозможна.")
        return 1
    print("[OK]   Все обязательные ctl* функции доступны")
    print("\nДрайвер и IGCL в порядке. Дальше: python -m arctuner telemetry")
    return 0


def _fmt(v: float | None, unit: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.2f}{unit}" if abs(v) < 1000 else f"{v:.0f}{unit}"


def cmd_telemetry(args: argparse.Namespace) -> int:
    """Снимок телеметрии (read-only). --csv для сохранения, --watch для опроса."""
    import csv
    import time
    from pathlib import Path

    from arctuner.igcl.api import IgclApi
    from arctuner.replay import IncidentReplay
    from arctuner.telemetry import PowerMeter, find_arc_adapter

    # Колонки CSV (стабильный порядок для анализа).
    csv_fields = [
        "timestamp",
        "gpu_freq_mhz",
        "gpu_voltage_v",
        "gpu_temp_c",
        "gpu_power_percent",
        "gpu_power_w",
        "card_power_w",
        "vram_power_w",
        "gpu_vr_temp_c",
        "vram_vr_temp_c",
        "sa_vr_temp_c",
        "vram_read_bandwidth_mbps",
        "vram_write_bandwidth_mbps",
        "gpu_over_voltage_percent",
        "vram_eff_freq_mbps",
        "vram_voltage_v",
        "vram_temp_c",
        "power_limited",
        "temperature_limited",
        "current_limited",
        "voltage_limited",
        "utilization_limited",
    ]

    def snapshot_row(t) -> dict[str, object]:
        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "gpu_freq_mhz": t.gpu_freq_mhz,
            "gpu_voltage_v": t.gpu_voltage_v,
            "gpu_temp_c": t.gpu_temp_c,
            "gpu_power_percent": t.gpu_power_percent,
            "gpu_power_w": t.gpu_power_w,
            "card_power_w": t.card_power_w,
            "vram_power_w": t.vram_power_w,
            "gpu_vr_temp_c": t.gpu_vr_temp_c,
            "vram_vr_temp_c": t.vram_vr_temp_c,
            "sa_vr_temp_c": t.sa_vr_temp_c,
            "vram_read_bandwidth_mbps": t.vram_read_bandwidth_mbps,
            "vram_write_bandwidth_mbps": t.vram_write_bandwidth_mbps,
            "gpu_over_voltage_percent": t.gpu_over_voltage_percent,
            "vram_eff_freq_mbps": t.vram_eff_freq_mbps,
            "vram_voltage_v": t.vram_voltage_v,
            "vram_temp_c": t.vram_temp_c,
            "power_limited": t.power_limited,
            "temperature_limited": t.temperature_limited,
            "current_limited": t.current_limited,
            "voltage_limited": t.voltage_limited,
            "utilization_limited": t.utilization_limited,
        }

    def emit_csv(path: str, row: dict[str, object]) -> None:
        p = Path(path)
        write_header = not p.exists() or p.stat().st_size == 0
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=csv_fields)
            if write_header:
                w.writeheader()
            w.writerow(row)

    watch_sec = getattr(args, "watch", None)
    csv_path = getattr(args, "csv", None)
    replay_size = getattr(args, "replay", 0) or 0
    replay: IncidentReplay | None = None

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена среди устройств.")
                return 1
            handle, info = found
            print(
                f"Устройство: {info.name} (0x{info.device_id:04X}, {info.frequency_mhz} МГц, "
                f"{info.num_xe_cores} Xe-cores)\n"
            )

            if watch_sec:
                print(f"Наблюдение каждые {watch_sec} сек (Ctrl+C для выхода)...")
            meter = PowerMeter()  # ватты из счётчиков энергии; первый тик — база
            if replay_size:
                replay = IncidentReplay(max_samples=replay_size, device=info.name)
                print(
                    f"Реплей инцидента: буфер {replay_size} снимков, дампы в "
                    f"~/.arctune/incidents (сброс счётчика = дамп автоматически)."
                )
            while True:
                t = meter.enrich(api.read_power_telemetry(handle))
                if replay is not None:
                    trigger = replay.push(t)
                    if trigger:
                        dump_path = replay.dump(trigger)
                        print(
                            f"  [INCIDENT] Счётчик энергии сброшен ({trigger}) — "
                            f"драйвер перезагрузился. Дамп: {dump_path}"
                        )
                row = snapshot_row(t)
                print("=== Телеметрия (read-only) ===")
                print(
                    f"  Ядро:   {_fmt(t.gpu_freq_mhz, ' МГц'):>12}  "
                    f"{_fmt(t.gpu_voltage_v, ' В'):>8}  {_fmt(t.gpu_temp_c, ' °C'):>7}  "
                    f"питание {_fmt(t.gpu_power_percent, '%'):>6}"
                    f" ({_fmt(t.gpu_power_w, ' Вт')})"
                )
                if t.gpu_power_w is None:
                    print("  Вт: считаются из счётчика энергии — появятся со 2-го тика (--watch).")
                print(
                    f"  Память: {_fmt(t.vram_eff_freq_mbps, ' Гбит/с'):>12}  "
                    f"{_fmt(t.vram_voltage_v, ' В'):>8}  {_fmt(t.vram_temp_c, ' °C'):>7}  "
                    f"{_fmt(t.vram_power_w, ' Вт')}"
                )
                bw = (
                    f"чтение {_fmt(t.vram_read_bandwidth_mbps, ' МБ/с')}  "
                    f"запись {_fmt(t.vram_write_bandwidth_mbps, ' МБ/с')}"
                )
                print(f"  Память, фактическая скорость: {bw}")
                vr = "/".join(
                    "—" if v is None else f"{v:.0f}"
                    for v in (t.gpu_vr_temp_c, t.vram_vr_temp_c, t.sa_vr_temp_c)
                )
                over = (
                    f"  надбавка напряжения {_fmt(t.gpu_over_voltage_percent, ' %')}"
                    if t.gpu_over_voltage_percent is not None
                    else ""
                )
                print(f"  VR-фазы (ядро/память/SA): {vr} °C{over}")
                print(f"\n  Ограничение: {t.limit_summary()}")

                if csv_path:
                    emit_csv(csv_path, row)
                    print(f"  -> CSV: {csv_path}")

                if not watch_sec:
                    break
                time.sleep(watch_sec)
                print()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        if replay is not None and replay.samples:
            path = replay.dump("manual_interrupt")
            print(f"Реплей инцидента сохранён по Ctrl+C: {path}")
    except Exception as exc:
        print(f"[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
        return 1
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    """Диапазоны пунктов разгона (read-only)."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.telemetry import find_arc_adapter

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, _ = found
            oc = api.read_oc_properties(handle)
            print("=== Пункты разгона (min / max / step / default) ===")
            print(f"  Разгон поддерживается: {oc['supported']}")
            for key, val in oc.items():
                if key == "supported" or not getattr(val, "supported", False):
                    continue
                print(
                    f"  {key:<25} {val.min:>8.2f} .. {val.max:<8.2f} "
                    f"step {val.step:<6.2f} def {val.default:.2f}"
                )
            print(
                "  ℹ️ VF-лимиты — рамки драйвера для построения кривой V/F. Запись"
                " ограничена жёсткими рамками проекта: 600…1250 мВ / 300…3300 МГц."
            )
            from arctuner import tuner

            current = tuner.current_values(api, handle)
            units = {
                "gpuFrequencyOffset": "МГц",
                "powerLimit": "%",
                "temperatureLimit": "°C",
                "vramMemSpeedLimit": "Гбит/с",
                "gpuVoltageOffset": "%",
            }
            print("\n=== Текущие значения (V2 GET) ===")
            for k, v in current.items():
                u = units.get(k, "")
                print(f"  {k:<25} {'—' if v is None else f'{v:g} {u}'}")
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """⚠️ Запись настроек. Требует явного подтверждения (меняет состояние карты)."""
    from arctuner import tuner
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.logbook import Logbook, default_logbook_path
    from arctuner.telemetry import find_arc_adapter
    from arctuner.tuner import SafetyLimitError

    # Собираем запрошенные изменения (только явно переданные флаги).
    changes: list[tuple[str, float, object]] = []
    if args.power_limit is not None:
        changes.append(("power_limit", args.power_limit, tuner.set_power_limit))
    if args.freq_offset is not None:
        changes.append(("freq_offset", args.freq_offset, tuner.set_gpu_freq_offset))
    if args.voltage_offset is not None:
        changes.append(("voltage_offset", args.voltage_offset, tuner.set_gpu_voltage_offset))
    if args.temp_limit is not None:
        changes.append(("temp_limit", args.temp_limit, tuner.set_temperature_limit))
    if args.vram_speed is not None:
        changes.append(("vram_speed", args.vram_speed, tuner.set_vram_speed))

    if not changes:
        print("Нечего менять: передайте хотя бы один параметр (--power-limit и т.п.).")
        print("Диапазоны: python -m arctuner status")
        return 2

    if not args.yes:
        print("⚠️  Это ИЗМЕНИТ настройки карты (write). Запрошено:")
        for name, value, _ in changes:
            print(f"    {name} = {value}")
        if any(
            name == "voltage_offset" or (name == "freq_offset" and value < 0)
            for name, value, _ in changes
        ):
            print(
                "    ℹ️ Будет вызван ctlOverclockWaiverSet — Intel требует его для записи"
                " напряжения/отрицательной частоты (принятие риска снижения срока службы)."
            )
        try:
            answer = input("Подтвердить запись? [y/N]: ")
        except EOFError:
            print("\nНеожиданный конец ввода — отмена записи.")
            return 1
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено — ничего не записано.")
            return 1

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, dev = found
            device_name = dev.name
            before = tuner.current_values(api, handle)
            print(f"\nБыло: {before}")
            log = Logbook(default_logbook_path())
            for _, value, fn in changes:
                if fn is tuner.set_vram_speed:
                    report = fn(api, handle, value, allow_beyond_nominal=args.force)
                else:
                    report = fn(api, handle, value)
                old_txt = "неизвестно" if report.old is None else f"{report.old:g}"
                line = f"  {report.field}: {old_txt} -> {report.new:g}"
                if report.note:
                    line += f"  ({report.note})"
                print(line)
                log.record(
                    report.field,
                    report.old,
                    report.new,
                    device=device_name,
                    note=report.note,
                )
            print("Готово. Проверьте: python -m arctuner telemetry")
            print(f"Журнал: {default_logbook_path()}")
    except SafetyLimitError as e:
        print(f"[BLOCK] {e}")
        return 1
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    except Exception as exc:
        print(f"[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
        return 1
    return 0


def cmd_vf(args: argparse.Namespace) -> int:
    """Кривая V/F: чтение/экспорт (read-only) / ⚠️ запись --apply (с валидацией)."""
    import csv
    from pathlib import Path

    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.telemetry import find_arc_adapter
    from arctuner.tuner import SafetyLimitError, VfPoint, apply_vf_curve, validate_vf_curve

    def read_curve(api: IgclApi, handle) -> list:
        return api.read_vf_curve(handle)

    def print_curve(pts: list) -> None:
        print(f"Точек: {len(pts)}")
        print(f"  {'мВ':>6}  {'МГц':>6}")
        for p in pts:
            print(f"  {p.voltage_mv:>6}  {p.freq_mhz:>6}")

    def export_csv(path: str, pts: list) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["voltage_mv", "freq_mhz"])
            for pt in pts:
                w.writerow([pt.voltage_mv, pt.freq_mhz])

    def load_csv(path: str) -> list[VfPoint]:
        out: list[VfPoint] = []
        with Path(path).open(encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                out.append(
                    VfPoint(voltage_mv=float(row["voltage_mv"]), freq_mhz=float(row["freq_mhz"]))
                )
        return out

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, _ = found

            if args.apply:
                points = load_csv(args.apply)
                print(f"Загружено {len(points)} точек из {args.apply}")
                # Сначала валидация БЕЗ записи — показать, что именно уйдёт.
                validate_vf_curve(points)
                print("Валидация OK. Кривая:")
                print_curve(points)
                if not args.yes:
                    print("\n⚠️  Это ЗАПИШЕТ кастомную кривую V/F (меняет карту).")
                    try:
                        ans = input("Подтвердить запись? [y/N]: ")
                    except EOFError:
                        print("\nНеожиданный конец ввода — отмена.")
                        return 1
                    if ans.strip().lower() not in ("y", "yes", "д", "да"):
                        print("Отменено — ничего не записано.")
                        return 1
                from arctuner.logbook import Logbook, default_logbook_path

                report = apply_vf_curve(api, handle, points)
                Logbook(default_logbook_path()).record(
                    report.field, report.old, report.new, note=report.note
                )
                print(f"Готово: {report.note}. Откат: python -m arctuner reset --oc")
                return 0

            # Обычное чтение (+ опциональный экспорт)
            pts = read_curve(api, handle)
            if not pts:
                print("[FAIL] Кривая V/F не читается на этом устройстве/драйвере.")
                return 1
            print_curve(pts)
            if args.csv:
                export_csv(args.csv, pts)
                print(f"\n-> CSV: {args.csv}")
                print(f"Запись той же кривой: python -m arctuner vf --apply {args.csv}")
    except SafetyLimitError as e:
        print(f"[BLOCK] {e}")
        return 1
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    except Exception as exc:
        print(f"[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
        return 1
    return 0


def cmd_autofind(args: argparse.Namespace) -> int:
    """⚠️ Автопоиск стабильной точки (меняет настройки, требует нагрузки).

    Использует тот же TuningEngine + GpuContext, что и GUI: каждая запись идёт
    через tuner-проверки и журнал (реальные gpuFrequencyOffset/gpuVoltageOffset),
    поэтому ``log --rollback`` работает и не знает полей frequency/undervolt.
    """
    from arctuner import autofind as af
    from arctuner.autotune.engine import TuningConfig, TuningEngine
    from arctuner.autotune.watchdog import Watchdog
    from arctuner.context import GpuContext
    from arctuner.igcl.api import IgclError
    from arctuner.logbook import default_logbook_path
    from arctuner.tuner import SafetyLimitError

    mode = args.mode
    # Шаг по умолчанию зависит от режима: 15 МГц для частоты, 1 % для undervolt.
    step = args.step if args.step is not None else (15.0 if mode == af.MODE_FREQUENCY else 1.0)
    cfg = TuningConfig(
        mode=mode,
        step=step,
        # Не даём шагу выродиться в бесконечную крутку у границы.
        min_step=max(step * 0.2, 0.1),
        max_steps=args.max_steps,
        settle_seconds=args.settle,
        temp_ceiling_c=args.temp_ceiling,
        require_load=not args.no_load_check,
        load_timeout=3.0,  # как в старом CLI: не ждать нагрузку минутами
    )

    print("⚠️  АВТОПОИСК стабильной точки — это ИЗМЕНИТ настройки карты.")
    print(f"  режим:      {mode}")
    print(f"  шаг:        {cfg.step}  (до {cfg.max_steps} шагов, min {cfg.min_step})")
    print(f"  потолок t°: {cfg.temp_ceiling_c} °C")
    print(
        f"  нагрузка:   {'требуется (запустите стресс/бенчмарк!)' if cfg.require_load else 'не проверяется (--no-load-check)'}"
    )
    if cfg.require_load:
        print("\n  ВАЖНО: запустите стресс-тест/игру в ДРУГОМ окне и держите его")
        print("  активным. Без нагрузки autofind остановится с 'нет нагрузки'.")
    print("  Watchdog ловит TDR/зависание телеметрии и откатывает шаг, но compute-")
    print("  ошибки приложения телеметрии не видны — следите за стресс-тестом.")
    if not args.yes:
        try:
            ans = input("\nПродолжить? [y/N]: ")
        except EOFError:
            print("\nНеожиданный конец ввода — отмена autofind.")
            return 1
        if ans.strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено — ничего не записано.")
            return 1

    def on_step(i: int, value: float, snap) -> None:
        print(
            f"  шаг {i:>3}: {value:>8.2f}  t={snap.gpu_temp_c}°C  "
            f"пит={snap.gpu_power_percent}%  [{snap.limit_summary()}]"
        )

    def on_status(text: str) -> None:
        print(f"  {text}")

    try:
        with GpuContext() as ctx:
            info = ctx.info
            device = getattr(info, "name", "") if info is not None else ""
            engine = TuningEngine(
                ctx,
                cfg,
                watchdog=Watchdog(device=device),
            )
            result = engine.run(on_step=on_step, on_status=on_status)

            print("\n=== Итог ===")
            print(f"  режим:       {result.mode}")
            print(f"  база:        {result.baseline:.2f}")
            print(f"  стабильно:   {result.best_stable:.2f}  (сдвиг {result.improvement:+.2f})")
            print(f"  шагов:       {result.steps_taken}")
            print(f"  причина:     {result.stopped_reason}")
            print(f"  журнал:      {default_logbook_path()}")
            if result.card_value is None:
                print(
                    "  ⚠️  Состояние карты НЕ подтверждено: откат/запись не удались."
                    " Проверьте `python -m arctuner status` и `telemetry`."
                )
    except af.AutofindAbortError as e:
        print(f"\n[ABORT] {e}")
        return 1
    except (IgclError, SafetyLimitError) as e:
        print(f"\n[FAIL] {e} (выполнен откат)")
        return 1
    except OSError as e:
        print(f"\n[FAIL] {e}")
        return 1
    except Exception as exc:
        print(f"\n[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
        return 1
    return 0


def cmd_info(_: argparse.Namespace) -> int:
    """PCIe/ReBAR, ECC, прошивка, режимы вентиляторов (read-only)."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.telemetry import find_arc_adapter

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, dev = found
            print(f"Устройство: {dev.name} (0x{dev.device_id:04X})")

            # PCIe — первопричина «разгон не помогает» (x8 вместо x16, ReBAR off).
            try:
                pci = api.get_pci_state(handle)
                print(
                    f"  PCIe: Gen {pci.gen} x{pci.width}  адрес {pci.address}  "
                    f"ReBAR: {'вкл' if pci.rebar_enabled else 'ВЫКЛ'}"
                    + ("" if pci.rebar_supported else " (не поддержан)")
                )
            except IgclError as e:
                print(f"  PCIe: нет данных ({e})")

            # Прошивка: на B580 драйвер отвечает UNKNOWN — честно сообщаем.
            try:
                fw = api.get_firmware_properties(handle)
                print(f"  Прошивка: {fw.name} / {fw.version}")
            except IgclError as e:
                print(f"  Прошивка: драйвер не сообщает ({e.code:#010x})")

            # ECC: на consumer-картах обычно UNSUPPORTED_FEATURE.
            try:
                ecc = api.get_ecc_state(handle)
                state = f"{ecc.current}" + (
                    f" → {ecc.pending} (после перезагрузки)" if ecc.pending != ecc.current else ""
                )
                print(f"  ECC: {'вкл' if ecc.current == 'enabled' else 'выкл'} ({state})")
            except IgclError as e:
                print(f"  ECC: не поддерживается ({e.code:#010x})")

            # Режимы вентиляторов.
            fans = api.enum_fans(handle)
            for i, fan in enumerate(fans):
                try:
                    cfg = api.get_fan_config(fan)
                    speed = api.get_fan_speed(fan)
                    note = f" (фикс {cfg.fixed_rpm} RPM)" if cfg.fixed_rpm else ""
                    print(f"  Вентилятор [{i}]: режим {cfg.mode}{note}, {speed} RPM")
                except IgclError as e:
                    print(f"  Вентилятор [{i}]: конфиг не читается ({e.code:#010x})")
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    except Exception as exc:
        print(f"[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
        return 1
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    """Журнал изменений: просмотр истории и откат к исходным значениям."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.logbook import Logbook, default_logbook_path
    from arctuner.telemetry import find_arc_adapter

    log = Logbook(default_logbook_path())

    if args.rollback:
        targets = log.rollback_targets()
        if not targets:
            print("Нечего откатывать — журнал пуст или нет исходных значений.")
            return 0
        if not args.yes:
            print("⚠️  Откат к исходным значениям журнала:")
            for k, v in targets.items():
                print(f"    {k} <- {v}")
            try:
                ans = input("Подтвердить? [y/N]: ")
            except EOFError:
                print("\nНеожиданный конец ввода — отмена отката.")
                return 1
            if ans.strip().lower() not in ("y", "yes", "д", "да"):
                print("Отменено.")
                return 1
        try:
            with IgclApi() as api:
                found = find_arc_adapter(api)
                if not found:
                    print("[FAIL] Дискретная Intel Arc не найдена.")
                    return 1
                handle, _ = found
                _build_rollback_map()
                for name, value in targets.items():
                    fn = _ROLLBACK_MAP.get(name)
                    if fn is None or value is None:
                        print(f"  {name}: нет функции отката, пропущено")
                        continue
                    fn(api, handle, value)
                    print(f"  {name} <- {value}  (восстановлено)")
                    log.mark_restored(name)
        except IgclError as e:
            print(f"[FAIL] {e}")
            return 1
        except Exception as exc:
            print(f"[UNEXPECTED] {exc}  ({type(exc).__module__}.{type(exc).__name__})")
            return 1
        print("Откат завершён.")
        return 0

    # Обычный просмотр
    entries = log.tail(args.n)
    if not entries:
        print(f"Журнал пуст: {default_logbook_path()}")
        return 0
    print(f"Последние {len(entries)} записей ({default_logbook_path()}):")
    for e in entries:
        flag = " [restored]" if e.restored else ""
        print(f"  {e.timestamp}  {e.field}: {e.old} -> {e.new}{flag}")
        if e.note:
            print(f"      {e.note}")
    print("\nОткат: python -m arctuner log --rollback")
    return 0  # иначе main() падает на int(None) — TypeError после печати


# Карта полей → функции восстановления (для rollback).
_ROLLBACK_MAP: dict[str, object] = {}


def _build_rollback_map() -> None:
    if not _ROLLBACK_MAP:
        from arctuner import tuner

        _ROLLBACK_MAP.update(
            {
                "powerLimit": tuner.set_power_limit,
                "gpuFrequencyOffset": tuner.set_gpu_freq_offset,
                "gpuVoltageOffset": tuner.set_gpu_voltage_offset,
                "temperatureLimit": tuner.set_temperature_limit,
                # Значение уже было на карте (записано ArcTune с --force):
                # повторное согласие при откате не требуется.
                "vramMemSpeedLimit": partial(tuner.set_vram_speed, allow_beyond_nominal=True),
            }
        )


def cmd_fans(args: argparse.Namespace) -> int:
    """Вентиляторы: чтение (по умолчанию) / ⚠️ фиксация скорости с --set."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.logbook import JournalWriteError, Logbook, default_logbook_path
    from arctuner.telemetry import find_arc_adapter
    from arctuner.tuner import SafetyLimitError

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, dev = found
            fans = api.enum_fans(handle)
            if not fans:
                print("Вентиляторы не найдены (возможно, ноутбук/внешний кулер).")
                return 0
            for i, f in enumerate(fans):
                p = api.get_fan_properties(f)
                modes = "/".join(
                    m
                    for bit, m in ((1, "default"), (2, "fixed"), (4, "table"))
                    if p.supportedModes & bit
                )
                speed = api.get_fan_speed(f)
                print(
                    f"  [{i}] {speed:>4} RPM (max {p.maxRPM})  "
                    f"режимы: {modes or '-'}  управляемый: {p.canControl}"
                )

            if args.set is not None:
                if not args.yes:
                    print(f"\n⚠️  Зафиксировать скорость ВСЕХ вентиляторов на {args.set} RPM?")
                    try:
                        ans = input("Подтвердить запись? [y/N]: ")
                    except EOFError:
                        print("\nНеожиданный конец ввода — отмена.")
                        return 1
                    if ans.strip().lower() not in ("y", "yes", "д", "да"):
                        print("Отменено.")
                        return 1
                log = Logbook(default_logbook_path())
                for i, f in enumerate(fans):
                    p = api.get_fan_properties(f)
                    if not p.canControl:
                        print(f"  [{i}] пропущен (не управляется)")
                        continue
                    if p.maxRPM <= 0:
                        print(f"  [{i}] BLOCK: maxRPM неизвестен — запись заблокирована")
                        continue
                    if args.set > p.maxRPM:
                        print(f"  [{i}] BLOCK: {args.set} > maxRPM {p.maxRPM}")
                        continue
                    old_rpm = api.get_fan_speed(f)
                    api.set_fan_fixed_speed(f, args.set)
                    try:
                        log.record(
                            f"fan[{i}]",
                            float(old_rpm),
                            float(args.set),
                            device=dev.name,
                            note="фиксация скорости",
                        )
                    except OSError as exc:
                        # Карта уже изменена — возвращаем авто, затем честно падаем.
                        try:
                            api.set_fan_default(f)
                            detail = "вентилятор возвращён в авто — настройка отменена"
                        except (IgclError, OSError) as rb_exc:
                            detail = f"вернуть авто не удалось — состояние неизвестно ({rb_exc})"
                        raise JournalWriteError(
                            f"fan[{i}]: журнал не записан ({exc}); {detail}"
                        ) from exc
                    print(f"  [{i}] установлено {args.set} RPM (было {old_rpm})")
                print("Вернуть авто: python -m arctuner reset --fans")
    except (IgclError, SafetyLimitError, OSError) as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """⚠️ Откат: OC-настроек к дефолту и/или вентиляторов в авто."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.logbook import (
        RESET_FANS_FIELD,
        RESET_OC_FIELD,
        Logbook,
        default_logbook_path,
    )
    from arctuner.telemetry import find_arc_adapter

    if not (args.oc or args.fans):
        print("Укажите, что сбрасывать: --oc и/или --fans")
        return 2

    if not args.yes:
        what = []
        if args.oc:
            what.append("все OC-настройки")
        if args.fans:
            what.append("вентиляторы -> авто")
        print(f"⚠️  Сброс: {', '.join(what)}")
        try:
            ans = input("Подтвердить? [y/N]: ")
        except EOFError:
            print("\nНеожиданный конец ввода — отмена сброса.")
            return 1
        if ans.strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, dev = found
            log = Logbook(default_logbook_path())
            if args.oc:
                from arctuner import tuner

                tuner.reset_to_default(api, handle)
                # Маркер инвалидирует прежние цели отката: карта уже в дефолте,
                # иначе `log --rollback` снова применил бы старый разгон.
                try:
                    log.record(
                        RESET_OC_FIELD,
                        None,
                        None,
                        device=dev.name,
                        note=(
                            "сброс OC к дефолту; прежние цели отката недействительны "
                            "(история сохранена)"
                        ),
                    )
                except OSError as exc:
                    print(
                        "[FAIL] OC сброшены к дефолту, но журнал не записан "
                        f"({exc}); прежние цели отката могли остаться."
                    )
                    return 1
                print("OC-настройки сброшены к дефолту (журнал: reset_oc).")
            if args.fans:
                for f in api.enum_fans(handle):
                    api.set_fan_default(f)
                try:
                    log.record(
                        RESET_FANS_FIELD,
                        None,
                        None,
                        device=dev.name,
                        note="вентиляторы возвращены в авто (история fan[*] сохранена)",
                    )
                except OSError as exc:
                    print(
                        "[FAIL] Вентиляторы возвращены в авто, но журнал не записан "
                        f"({exc}); история сброса не зафиксирована."
                    )
                    return 1
                print("Вентиляторы возвращены в аппаратный режим (журнал: reset_fans).")
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    except OSError as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


def _version() -> str:
    """Версия пакета; для frozen-exe без metadata — из arctuner.__version__."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("arctune")
    except PackageNotFoundError:
        from arctuner import __version__

        return __version__


def _configure_console() -> None:
    """Принудительно включает UTF-8 в консоли Windows (иначе кракозябры).

    Работает и для python-запуска, и для frozen-exe (PyInstaller).
    Все ошибки глотаем — не должны ломать работу из-за особенностей терминала.
    """
    if sys.platform != "win32":
        return
    import contextlib
    import ctypes

    with contextlib.suppress(Exception):
        # CP_UTF8 = 65001 — перекодируем консольные буферы ввода/вывода.
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arctune", description="ArcTune — IGCL для Intel Arc")
    p.add_argument("--version", action="version", version=f"arctune {_version()}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="проверка окружения").set_defaults(func=cmd_doctor)
    ptelem = sub.add_parser("telemetry", help="телеметрия (read-only)")
    ptelem.add_argument("--csv", metavar="FILE", help="дополнительно сохранить снимок в CSV")
    ptelem.add_argument("-w", "--watch", type=int, metavar="SEC", help="повторять каждые N сек")
    ptelem.add_argument(
        "--replay",
        nargs="?",
        const=600,
        default=0,
        type=int,
        metavar="N",
        help="буфер последних N снимков (по умолчанию 600); сброс счётчика "
        "энергии или Ctrl+C пишет дамп в ~/.arctune/incidents",
    )
    ptelem.set_defaults(func=cmd_telemetry)
    sub.add_parser("status", help="диапазоны разгона (read-only)").set_defaults(func=cmd_status)

    plog = sub.add_parser("log", help="журнал изменений / откат")
    plog.add_argument("-n", type=int, default=10, help="показать последние N записей")
    plog.add_argument("--rollback", action="store_true", help="откатить к исходным значениям")
    plog.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    plog.set_defaults(func=cmd_log)

    pvf = sub.add_parser("vf", help="кривая V/F: чтение/экспорт / ⚠️ --apply запись")
    pvf.add_argument("--csv", metavar="FILE", help="экспортировать текущую кривую в CSV")
    pvf.add_argument("--apply", metavar="FILE", help="⚠️ записать кривую из CSV (с валидацией)")
    pvf.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    pvf.set_defaults(func=cmd_vf)

    paf = sub.add_parser("autofind", help="⚠️ автопоиск стабильной точки (меняет карту)")
    paf.add_argument(
        "--mode",
        choices=["undervolt", "frequency"],
        default="frequency",
        help="что двигать (по умолчанию frequency: undervolt через offset на B580 ограничен)",
    )
    paf.add_argument(
        "--step",
        type=float,
        default=None,
        help="шаг: 15 МГц для frequency (по умолчанию), 1%% для undervolt",
    )
    paf.add_argument("--max-steps", type=int, default=20, help="максимум шагов")
    paf.add_argument("--settle", type=float, default=1.0, help="пауза после шага, сек")
    paf.add_argument("--temp-ceiling", type=float, default=80.0, help="откат при температуре >")
    paf.add_argument(
        "--no-load-check",
        action="store_true",
        help="не требовать нагрузку GPU (не рекомендуется)",
    )
    paf.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    paf.set_defaults(func=cmd_autofind)

    pf = sub.add_parser("fans", help="вентиляторы: чтение / --set RPM (⚠️ запись)")
    pf.add_argument("--set", type=int, help="зафиксировать скорость, RPM (⚠️)")
    pf.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    pf.set_defaults(func=cmd_fans)

    pi = sub.add_parser("info", help="PCIe / ReBAR / ECC / прошивка / режимы вентиляторов")
    pi.set_defaults(func=cmd_info)

    pr = sub.add_parser("reset", help="⚠️ откат к дефолту")
    pr.add_argument("--oc", action="store_true", help="сбросить OC-настройки")
    pr.add_argument("--fans", action="store_true", help="вентиляторы в авто")
    pr.add_argument("--yes", "-y", action="store_true", help="без подтверждения")
    pr.set_defaults(func=cmd_reset)

    ps = sub.add_parser("set", help="⚠️ установка настроек (меняет карту)")
    ps.add_argument("--vram-speed", type=float, help="скорость памяти, Гбит/с")
    ps.add_argument("--power-limit", type=float, help="лимит питания, %%")
    ps.add_argument("--voltage-offset", type=float, help="смещение напряжения ядра, %%")
    ps.add_argument("--freq-offset", type=float, help="смещение частоты ядра, МГц")
    ps.add_argument("--temp-limit", type=float, help="лимит температуры, °C")
    ps.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="пропустить подтверждение (для скриптов)",
    )
    ps.add_argument(
        "--force",
        action="store_true",
        help="разрешить vram_speed выше номинала 20 Гбит/с (до 22)",
    )
    ps.set_defaults(func=cmd_set)
    return p


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
