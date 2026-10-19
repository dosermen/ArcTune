"""Стресс-нагрузка и проверка нагрузки для AutoTune.

Проверка нагрузки — по дельте activity_counter из телеметрии (тот же
критерий, что у autofind._wait_for_load). Чтение передаётся как колбэк
`read_telemetry`, поэтому модуль не привязан к IgclApi напрямую.

Внешний стресс-нагрузчик запускается один раз перед поиском (external_cmd
в TuningConfig) и останавливается ПОСЛЕ поиска. На Windows потомки
cmd.exe (shell=True) не умирают от terminate() — используется taskkill /T.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable

from arctuner.igcl.api import TelemetrySnapshot

# Запуск из GUI: окно консоли не должно вспыхивать (на не-Windows — 0).
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _activity(read_telemetry: Callable[[], TelemetrySnapshot]) -> int | None:
    """Считывает activity_counter через переданный источник телеметрии.

    Сбой чтения телеметрии — не «нет нагрузки»: ошибка пробрасывается
    вызывающему (правило проекта: ошибки IGCL не глотаются).
    """
    return read_telemetry().activity_counter


def verify_stress(
    read_telemetry: Callable[[], TelemetrySnapshot],
    *,
    interval: float = 0.5,
    timeout: float = 6.0,
) -> bool:
    """Проверка: GPU под нагрузкой? (без запуска процессов, только замер).

    Args:
        read_telemetry: колбэк-источник снимков телеметрии.
        interval: пауза между замерами.
        timeout: максимум ожидания роста активности; 0 — одно чтение
            с паузой 0.1 с (activity_counter обновляется не мгновенно).

    Returns:
        True, если activity_counter вырос хотя бы раз (GPU реально работает).
    """
    if timeout <= 0:
        # Одно чтение с паузой — activity_counter обновляется драйвером не мгновенно.
        t1 = read_telemetry()
        time.sleep(0.1)
        t2 = read_telemetry()
        return bool(
            t1.activity_counter is not None
            and t2.activity_counter is not None
            and t2.activity_counter > t1.activity_counter
        )

    deadline = time.monotonic() + timeout
    prev = _activity(read_telemetry)
    while time.monotonic() < deadline:
        time.sleep(max(interval, 0.05))
        cur = _activity(read_telemetry)
        if prev is not None and cur is not None and cur > prev:
            return True
        prev = cur
    return False


def launch_stress(
    read_telemetry: Callable[[], TelemetrySnapshot],
    *,
    external_cmd: str | None = None,
    settle_time: float = 2.0,
    verify_interval: float = 1.0,
    verify_timeout: float = 60.0,
) -> bool:
    """Запускает стресс-нагрузку (если задана команда) и ждёт подтверждения.

    Args:
        read_telemetry: колбэк-источник снимков телеметрии.
        external_cmd: команда стресса; None — пользователь запускает сам.
        settle_time: пауза перед первой проверкой (дать процессу стартовать).
        verify_interval: интервал опроса activity_counter.
        verify_timeout: максимум времени на подтверждение нагрузки.

    Returns:
        True, если GPU начал работать.

    Raises:
        OSError: внешний процесс не удалось запустить (cmd=None — нет).
    """
    if external_cmd and start_external(external_cmd) is None:
        raise OSError(f"не удалось запустить стресс-нагрузчик: {external_cmd}")
    time.sleep(settle_time)
    deadline = time.monotonic() + verify_timeout
    prev = _activity(read_telemetry)
    while time.monotonic() < deadline:
        time.sleep(verify_interval)
        cur = _activity(read_telemetry)
        if prev is not None and cur is not None and cur > prev:
            return True
        prev = cur
    return False


def start_external(cmd: str) -> subprocess.Popen | None:
    """Запускает внешний стресс-нагрузчик.

    Returns:
        Объект процесса; None — запуск не удался (файл не найден и т.п.).
    """
    try:
        return subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def terminate_stress(proc: subprocess.Popen) -> None:
    """Останавливает стресс-нагрузчик вместе с дочерними процессами.

    Windows: shell=True держит cmd.exe-прослойку, terminate() убивает только
    её — потомки остаются. taskkill /T снимает всё дерево по PID.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=10.0,
            creationflags=_NO_WINDOW,
        )
    else:
        proc.terminate()
    proc.wait(timeout=5.0)


def wait_for_load(
    read_telemetry: Callable[[], TelemetrySnapshot],
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
) -> bool:
    """Ждёт появления нагрузки (обёртка verify_stress, совместимый интерфейс)."""
    return verify_stress(read_telemetry, interval=interval, timeout=timeout)
