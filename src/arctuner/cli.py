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


def cmd_telemetry(_: argparse.Namespace) -> int:
    """Снимок телеметрии (read-only)."""
    from arctuner.igcl.api import IgclApi, IgclError
    from arctuner.telemetry import find_arc_adapter

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

            t = api.read_power_telemetry(handle)
            print("=== Телеметрия (read-only) ===")
            print(
                f"  Ядро:   {_fmt(t.gpu_freq_mhz, ' МГц'):>12}  "
                f"{_fmt(t.gpu_voltage_v, ' В'):>8}  {_fmt(t.gpu_temp_c, ' °C'):>7}  "
                f"питание {_fmt(t.gpu_power_percent, '%'):>6}"
            )
            print(
                f"  Память: {_fmt(t.vram_eff_freq_mbps, ' МТ/с'):>12}  "
                f"{_fmt(t.vram_voltage_v, ' В'):>8}  {_fmt(t.vram_temp_c, ' °C'):>7}"
            )
            print(f"\n  Ограничение: {t.limit_summary()}")
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
    print("⚠️  set пока не реализован — защита от случайной записи.")
    print("См. docs/safety.md и docs/roadmap.md.")
    if args.vram_speed:
        print(f"(запрошено: vram_speed={args.vram_speed})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arctune", description="ArcTune — IGCL для Intel Arc")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="проверка окружения").set_defaults(func=cmd_doctor)
    sub.add_parser("telemetry", help="телеметрия (read-only)").set_defaults(func=cmd_telemetry)
    sub.add_parser("status", help="диапазоны разгона (read-only)").set_defaults(func=cmd_status)

    ps = sub.add_parser("set", help="⚠️ установка настроек (меняет карту)")
    ps.add_argument("--vram-speed", type=float, help="скорость памяти, Гбит/с")
    ps.add_argument("--power-limit", type=float, help="лимит питания, %%")
    ps.add_argument("--voltage-offset", type=float, help="смещение напряжения ядра, %%")
    ps.add_argument("--freq-offset", type=float, help="смещение частоты ядра, МГц")
    ps.set_defaults(func=cmd_set)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
