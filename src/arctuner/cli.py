"""CLI ArcTune.

Команды:
    doctor      — проверка окружения (драйвер, IGCL.dll, функции)
    telemetry   — снимок телеметрии (read-only)
    status      — текущие настройки разгона (read-only)
    set         — установка настроек (⚠️ меняет состояние карты)

Запись настроек (set) изолирована в tuner.py и проходит проверки диапазонов.
"""

from __future__ import annotations

import argparse
import sys


def cmd_doctor(_: argparse.Namespace) -> int:
    """Проверяет, видит ли система IGCL.dll и ключевые функции."""
    from arctuner.igcl.loader import IgclLoader, IgclNotFoundError

    try:
        loader = IgclLoader()
    except IgclNotFoundError as e:
        print(f"[FAIL] {e}")
        return 1

    info = loader.healthcheck()
    print(f"[OK]   IGCL.dll: {info['path']}")
    missing = info["missing_required"]
    if missing:
        print(f"[WARN] Отсутствуют функции (старый драйвер?): {', '.join(missing)}")
    else:
        print("[OK]   Все обязательные ctl* функции доступны")
    print("\nДрайвер и IGCL в порядке. Дальше: python -m arctuner telemetry")
    return 0


def cmd_telemetry(_: argparse.Namespace) -> int:
    """Read-only снимок телеметрии. TODO после реализации enumerate+telemetry."""
    print("telemetry: реализуется в ближайшем MVP (см. docs/roadmap.md).")
    print("Пока можно пользоваться GPU-Z для проверки датчиков.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    print("status: реализуется в ближайшем MVP (см. docs/roadmap.md).")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    print("⚠️  set пока не реализован — это защита от случайной записи.")
    print("См. docs/safety.md и docs/roadmap.md.")
    if args.vram_speed:
        print(f"(запрошено: vram_speed={args.vram_speed})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arctune", description="ArcTune — IGCL для Intel Arc")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="проверка окружения").set_defaults(func=cmd_doctor)
    sub.add_parser("telemetry", help="телеметрия (read-only)").set_defaults(func=cmd_telemetry)
    sub.add_parser("status", help="настройки разгона (read-only)").set_defaults(func=cmd_status)

    ps = sub.add_parser("set", help="⚠️ установка настроек (меняет карту)")
    ps.add_argument("--vram-speed", type=float, help="скорость памяти, Гбит/с")
    ps.add_argument("--power-limit", type=float, help="лимит питания, %")
    ps.add_argument("--voltage-offset", type=float, help="смещение напряжения ядра, %")
    ps.add_argument("--freq-offset", type=float, help="смещение частоты ядра, МГц")
    ps.set_defaults(func=cmd_set)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
