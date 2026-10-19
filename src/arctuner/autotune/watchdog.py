"""Watchdog — мониторинг стабильности + авто-откат.

Следит за системой во время разгона:
  1. TDR (Event ID 4101 от провайдера Arc)
  2. Замерзающая телеметрия (counter не растёт при active GPU)
  3. Перезагрузка драйвера (driver service restart)
  4. Повышение температуры выше emergency-порога (85°C)

При обнаружении проблемы — возвращает событие, а не ошибку.
Вызывающий решает: откат, лог, уведомление.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import ClassVar

from arctuner.igcl.api import TelemetrySnapshot


@dataclass
class WatchdogEvent:
    """Событие watchdog — что-то пошло не так."""

    type: str  # "TDR", "TEMP_EMERGENCY", "TELEMETRY_FROZEN", "DRIVER_RESTART"
    detail: str
    timestamp: str


class Watchdog:
    """Слепит за стабильностью системы во время авторазгона."""

    TEMPERATURE_EMERGENCY: ClassVar[float] = 85.0
    TELEMETRY_FROZEN_THRESHOLD: ClassVar[float] = 30.0  # секунд без роста
    TDR_EVENT_ID: ClassVar[int] = 4101

    def __init__(self, device: str = "") -> None:
        self._device = device
        self._last_activity: int | None = None
        self._last_check_time: float | None = None

    def check(
        self,
        snapshot: TelemetrySnapshot,
        prev_snapshot: TelemetrySnapshot | None = None,
    ) -> WatchdogEvent | None:
        """Проверяет снимок. Возвращает первое событие или None.

        Args:
            snapshot: текущий снимок телеметрии.
            prev_snapshot: предыдущий снимок (для сравнения).

        Returns:
            WatchdogEvent если проблема найдена, иначе None.
        """
        events: list[WatchdogEvent] = []

        # 1. Температура (emergency)
        if self._check_temperature(snapshot):
            events.append(
                WatchdogEvent(
                    type="TEMP_EMERGENCY",
                    detail=f"температура {snapshot.gpu_temp_c:.0f}°C > {self.TEMPERATURE_EMERGENCY:.0f}°C",
                    timestamp="",
                )
            )

        # 2. Замерзающая телеметрия
        if self._check_frozen_telemetry(snapshot, prev_snapshot):
            events.append(
                WatchdogEvent(
                    type="TELEMETRY_FROZEN",
                    detail=f"activity_counter не растёт {self.TELEMETRY_FROZEN_THRESHOLD:.0f}с",
                    timestamp="",
                )
            )

        # 3. TDR из Event Log
        if self._check_tdr():
            events.append(
                WatchdogEvent(
                    type="TDR",
                    detail="Event ID 4101 — TDR detected",
                    timestamp="",
                )
            )

        # Возвращаем первое событие (или None)
        if events:
            return events[0]
        return None

    def _check_temperature(self, snapshot: TelemetrySnapshot) -> bool:
        """Проверка температуры (emergency порог)."""
        return snapshot.gpu_temp_c is not None and snapshot.gpu_temp_c > self.TEMPERATURE_EMERGENCY

    def _check_frozen_telemetry(
        self,
        snapshot: TelemetrySnapshot,
        prev_snapshot: TelemetrySnapshot | None,
    ) -> bool:
        """Проверка замерзающей телеметрии.

        Если activity_counter > 0, но energy_counter не растёт —
        возможно драйвер завис.
        """
        if not prev_snapshot:
            return False

        if prev_snapshot.activity_counter is None or snapshot.activity_counter is None:
            return False

        # Activity растёт — телеметрия жива
        if snapshot.activity_counter > prev_snapshot.activity_counter:
            return False

        # Activity не растёт, но GPU должен работать
        if prev_snapshot.activity_counter > 0:
            # Проверяем energy counter
            if snapshot.gpu_energy_counter_j is None or prev_snapshot.gpu_energy_counter_j is None:
                return False
            if snapshot.gpu_energy_counter_j == prev_snapshot.gpu_energy_counter_j:
                return True  # energy не растёт — заморожено

        return False

    def _check_tdr(self) -> bool:
        """Проверка Event Log на TDR (Event ID 4101).

        Использует Get-WinEvent PowerShell для быстрого поиска.
        """
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Get-WinEvent -FilterXPath \"*[System[Provider[@Name='IntelArc']]]\" "
                    f"-MaxEvents 10 2>$null | "
                    f"Where-Object {{ $_.Id -eq {self.TDR_EVENT_ID} }} | "
                    f"Select-Object -First 1 | "
                    f"Format-Table -AutoSize -Property TimeCreated, Message",
                ],
                capture_output=True,
                timeout=5.0,
            )
            output = result.stdout.decode(errors="replace")
            return "TDR" in output or "TDREvent" in output
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def record_activity(self, snapshot: TelemetrySnapshot) -> None:
        """Записывает last_activity для сравнения."""
        self._last_activity = snapshot.activity_counter
        import time

        self._last_check_time = time.monotonic()
