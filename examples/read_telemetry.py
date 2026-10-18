"""Пример: проверка окружения и чтение телеметрии (read-only, безопасно).

Запуск:
    python examples/read_telemetry.py
"""

from __future__ import annotations

from arctuner.igcl.loader import IgclLoader, IgclNotFoundError


def main() -> None:
    print("=== ArcTune: проверка окружения ===")
    try:
        loader = IgclLoader()
    except IgclNotFoundError as e:
        print(f"[FAIL] {e}")
        return

    info = loader.healthcheck()
    print(f"IGCL.dll: {info['path']}")
    missing = info["missing_required"]
    if missing:
        print(f"[WARN] отсутствуют функции: {', '.join(missing)}")
    else:
        print("[OK] обязательные ctl* функции на месте")

    print("\nДальше (после реализации MVP):")
    print("  - ctlEnumerateDevices  → найти B580 (device_id=0xE20B)")
    print("  - ctlPowerTelemetryGet → ватты/°C/частоты + причины ограничения")


if __name__ == "__main__":
    main()
