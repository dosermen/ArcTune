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
        print(f"[WARN] Отсутствуют функции (старый драйвер?): {', '.join(missing)}")
    else:
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

    from arctuner.igcl.api import IgclApi, IgclError
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
            while True:
                t = meter.enrich(api.read_power_telemetry(handle))
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
    except IgclError as e:
        print(f"[FAIL] {e}")
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
                    f"  {key:<22} {val.min:>8.2f} .. {val.max:<8.2f} "
                    f"step {val.step:<6.2f} def {val.default:.2f}"
                )
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
        answer = input("Подтвердить запись? [y/N]: ").strip().lower()
        if answer not in ("y", "yes", "д", "да"):
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
                line = f"  {report.field}: {report.old} -> {report.new}"
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
            print(f"  {p.voltage:>6}  {p.frequency:>6}")

    def export_csv(path: str, pts: list) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["voltage_mv", "freq_mhz"])
            for pt in pts:
                w.writerow([pt.voltage, pt.frequency])

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
                    ans = input("Подтвердить запись? [y/N]: ").strip().lower()
                    if ans not in ("y", "yes", "д", "да"):
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
    return 0


def cmd_autofind(args: argparse.Namespace) -> int:
    """⚠️ Автопоиск стабильной точки (меняет настройки, требует нагрузки)."""
    from arctuner import autofind as af
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.logbook import Logbook, default_logbook_path
    from arctuner.telemetry import find_arc_adapter
    from arctuner.tuner import SafetyLimitError

    mode = af.MODE_FREQUENCY if args.mode == "frequency" else af.MODE_UNDERVOLT
    cfg = af.AutofindConfig(
        mode=mode,
        step=args.step,
        max_steps=args.max_steps,
        settle_seconds=args.settle,
        temp_ceiling_c=args.temp_ceiling,
        require_load=not args.no_load_check,
    )

    print("⚠️  АВТОПОИСК стабильной точки — это ИЗМЕНИТ настройки карты.")
    print(f"  режим:      {mode}")
    print(f"  шаг:        {cfg.step}  (до {cfg.max_steps} шагов)")
    print(f"  потолок t°: {cfg.temp_ceiling_c} °C")
    print(
        f"  нагрузка:   {'требуется (запустите стресс/бенчмарк!)' if cfg.require_load else 'не проверяется (--no-load-check)'}"
    )
    if cfg.require_load:
        print("\n  ВАЖНО: запустите стресс-тест/игру в ДРУГОМ окне и держите его")
        print("  активным. Без нагрузки autofind остановится с 'нет нагрузки'.")
        print("  Телеметрия не видит крэши (TDR) — при сбое просто перезагрузите")
        print("  систему, настройки вернутся к дефолту при старте драйвера.")
    if not args.yes:
        ans = input("\nПродолжить? [y/N]: ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            print("Отменено — ничего не записано.")
            return 1

    def on_step(i: int, value: float, snap) -> None:
        print(
            f"  шаг {i:>3}: {value:>8.2f}  t={snap.gpu_temp_c}°C  "
            f"пит={snap.gpu_power_percent}%  [{snap.limit_summary()}]"
        )

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, _ = found
            result = af.autofind(api, handle, cfg, on_step=on_step)

            print("\n=== Итог ===")
            print(f"  база:        {result.baseline:.2f}")
            print(f"  стабильно:   {result.best_stable:.2f}  (сдвиг {result.improvement:+.2f})")
            print(f"  шагов:       {result.steps_taken}")
            print(f"  причина:     {result.stopped_reason}")
            Logbook(default_logbook_path()).record(
                result.mode, result.baseline, result.best_stable, note=result.stopped_reason
            )
            print(f"  журнал:      {default_logbook_path()}")
    except af.AutofindAbortError as e:
        print(f"\n[ABORT] {e}")
        return 1
    except (IgclError, SafetyLimitError) as e:
        print(f"\n[FAIL] {e} (выполнен откат)")
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
            ans = input("Подтвердить? [y/N]: ").strip().lower()
            if ans not in ("y", "yes", "д", "да"):
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
                "vramMemSpeedLimit": tuner.set_vram_speed,
            }
        )


def cmd_fans(args: argparse.Namespace) -> int:
    """Вентиляторы: чтение (по умолчанию) / ⚠️ фиксация скорости с --set."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.telemetry import find_arc_adapter
    from arctuner.tuner import SafetyLimitError

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, _ = found
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
                    ans = input("Подтвердить запись? [y/N]: ").strip().lower()
                    if ans not in ("y", "yes", "д", "да"):
                        print("Отменено.")
                        return 1
                for i, f in enumerate(fans):
                    p = api.get_fan_properties(f)
                    if not p.canControl:
                        print(f"  [{i}] пропущен (не управляется)")
                        continue
                    if args.set > p.maxRPM > 0:
                        print(f"  [{i}] BLOCK: {args.set} > maxRPM {p.maxRPM}")
                        continue
                    api.set_fan_fixed_speed(f, args.set)
                    print(f"  [{i}] установлено {args.set} RPM")
                print("Вернуть авто: python -m arctuner reset --fans")
    except (IgclError, SafetyLimitError) as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """⚠️ Откат: OC-настроек к дефолту и/или вентиляторов в авто."""
    from arctuner.igcl.api import IgclApi, IgclError
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
        ans = input("Подтвердить? [y/N]: ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    try:
        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] Дискретная Intel Arc не найдена.")
                return 1
            handle, _ = found
            if args.oc:
                from arctuner import tuner

                tuner.reset_to_default(api, handle)
                print("OC-настройки сброшены к дефолту.")
            if args.fans:
                for f in api.enum_fans(handle):
                    api.set_fan_default(f)
                print("Вентиляторы возвращены в аппаратный режим.")
    except IgclError as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


def _version() -> str:
    """Версия пакета; для frozen-exe без metadata — из pyproject-фолбэка."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("arctune")
    except PackageNotFoundError:
        return "0.1.0"


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
    ptelem.add_argument("--once", action="store_true", help="один снимок (без -w)")
    ptelem.add_argument("-w", "--watch", type=int, metavar="SEC", help="повторять каждые N сек")
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
        default="undervolt",
        help="что двигать (по умолчанию undervolt — безопасное направление)",
    )
    paf.add_argument("--step", type=float, default=1.0, help="шаг (%% undervolt / МГц частоты)")
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
