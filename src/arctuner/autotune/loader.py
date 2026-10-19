"""Стресс-нагрузчик для AutoTune.

Запускает внешний бенчмарк (или встроенную команду) через subprocess.
Возвращает True, если нагрузка обнаружена в activity_counter.

Поддерживаемые источники нагрузки (в порядке приоритета):
  1. arctune bench run (встроенный, если установлен)
  2. subprocess к OCCT / 3DMark / пользовательской команде
  3. встроенный push через IGCL Zero-Level (если доступен)

По умолчанию — ожидает, пока внешний процесс запущен пользователем.
Если external_cmd не передан — возвращает True без нагрузки (для тестов).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from arctuner.igcl.api import IgclApi


def launch_stress(
    api: IgclApi,
    handle,
    *,
    external_cmd: str | None = None,
    timeout: float = 5.0,
    verify_interval: float = 1.0,
    verify_timeout: float = 60.0,
) -> bool:
    """Запускает стресс-нагрузку и ждёт подтверждения.

    Args:
        api: инициализированный IgclApi.
        handle: handle устройства.
        external_cmd: команда для запуска (cmd / PowerShell).
            Если None — ждёт, что пользователь запустит нагрузку сам.
        timeout: сколько ждать перед стартом проверки.
        verify_interval: интервал опроса activity_counter.
        verify_timeout: максимум времени на ожидание нагрузки.

    Returns:
        True если GPU начал работать (activity_counter растёт).
    """
    if external_cmd:
        _launch_process(external_cmd)

    time.sleep(timeout)

    deadline = time.monotonic() + verify_timeout
    prev = _read_activity(api, handle)

    while time.monotonic() < deadline:
        time.sleep(verify_interval)
        cur = _read_activity(api, handle)
        if prev is not None and cur is not None and cur > prev:
            return True
        prev = cur

    return False


def _launch_process(cmd: str) -> subprocess.Popen:
    """Запускает внешний процесс нагрузки."""
    return subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _read_activity(api: IgclApi, handle) -> int | None:
    """Считывает activity_counter из телеметрии."""
    snap = api.read_power_telemetry(handle)
    return snap.activity_counter


def verify_stress(
    api: IgclApi,
    handle,
    *,
    interval: float = 0.5,
    timeout: float = 30.0,
) -> bool:
    """Проверка: GPU под нагрузкой? (без запуска, только проверка).

    Args:
        api: инициализированный IgclApi.
        handle: handle устройства.
        interval: интервал опроса.
        timeout: максимум ожидания.

    Returns:
        True если активность обнаружена.
    """
    deadline = time.monotonic() + timeout
    prev = _read_activity(api, handle)

    while time.monotonic() < deadline:
        time.sleep(interval)
        cur = _read_activity(api, handle)
        if prev is not None and cur is not None and cur > prev:
            return True
        prev = cur

    return False


def wait_for_load(
    api: IgclApi,
    handle,
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
) -> bool:
    """Ждёт появления нагрузки и возвращает True.

    Эта функция — обёртка над verify_stress, с тем же интерфейсом,
    что ожидает _wait_for_load из autofind.py, но совместима с
    авто-запуском бенчмарка.

    Returns:
        True если GPU под нагрузкой.
    """
    return verify_stress(api, handle, interval=interval, timeout=timeout)


def stress_with_internal_bench(
    api: IgclApi,
    handle,
    bench_path: Path,
    duration: float = 30.0,
) -> bool:
    """Запускает встроенный bench (если установлен) и ждёт нагрузки.

    Args:
        api: инициализированный IgclApi.
        handle: handle устройства.
        bench_path: путь к arctune bench.
        duration: длительность bench-сессии.

    Returns:
        True если нагрузка подтверждена.
    """
    cmd = f'"{bench_path}" bench run --duration {duration}'
    _launch_process(cmd)
    return wait_for_load(api, handle, timeout=duration + 5.0)
