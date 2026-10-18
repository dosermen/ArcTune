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

    # Теперь читаем телеметрию напрямую через API (read-only).
    print("\n=== Чтение телеметрии (read-only) ===")
    try:
        from arctuner.igcl.api import IgclApi
        from arctuner.telemetry import find_arc_adapter

        with IgclApi() as api:
            found = find_arc_adapter(api)
            if not found:
                print("[FAIL] дискретная Intel Arc не найдена")
                return
            handle, dev = found
            print(f"Устройство: {dev.name} (0x{dev.device_id:04X})")
            t = api.read_power_telemetry(handle)
            print(
                f"  ядро: {t.gpu_freq_mhz} МГц, {t.gpu_voltage_v} В, "
                f"{t.gpu_temp_c} °C, питание {t.gpu_power_percent}%"
            )
            print(
                f"  память: {t.vram_eff_freq_mbps} Гбит/с, {t.vram_voltage_v} В, {t.vram_temp_c} °C"
            )
            print(f"  ограничение: {t.limit_summary()}")
    except Exception as e:
        print(f"[FAIL] {e}")


if __name__ == "__main__":
    main()
