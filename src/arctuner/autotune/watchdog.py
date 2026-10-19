"""Watchdog — мониторинг стабильности системы во время автопоиска.

Что видит и чего не видит:
  - Температура выше аварийного порога (85 °C) — инцидент, откат.
  - «Замороженная» телеметрия: activity_counter стоит (GPU был под
    нагрузкой и должен работать), а счётчик энергии не растёт —
    признак зависшего драйвера/сбоя.
  - TDR: Event ID 4101 в журнале System (пишет провайдер «Display» —
    «Видеодрайвер перестал отвечать и был успешно восстановлен»).
    Ищутся ТОЛЬКО события после запуска watchdog, иначе найдутся
    старые TDR недельной давности.

Чего телеметрия НЕ видит: compute-ошибки и крэши приложений — автопоиск
опирается на watched-нагрузку из внешнего бенчмарка.

При обнаружении проблемы возвращает WatchdogEvent, а не ошибку:
решение (откат, лог, уведомление) — за движком.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from arctuner.igcl.api import TelemetrySnapshot


@dataclass(frozen=True)
class WatchdogEvent:
    """Событие watchdog — что-то пошло не так."""

    type: str  # "TEMP_EMERGENCY" | "TELEMETRY_FROZEN" | "TDR"
    detail: str


class Watchdog:
    """Следит за стабильностью системы во время авторазгона."""

    TEMPERATURE_EMERGENCY: ClassVar[float] = 85.0  # °C — авария, дальше искать нечего
    TDR_EVENT_ID: ClassVar[int] = 4101
    TDR_PROVIDER: ClassVar[str] = "Display"  # источник 4101 в журнале System

    def __init__(self, device: str = "") -> None:
        self._device = device
        self._started_at = datetime.now()
        self._last_snap: TelemetrySnapshot | None = None
        self._tdr_found = False  # однажды найденный TDR не ищется повторно

    def check(self, snapshot: TelemetrySnapshot) -> WatchdogEvent | None:
        """Проверяет снимок. Возвращает первое событие или None.

        Args:
            snapshot: текущий снимок телеметрии (предыдущий следит сам).

        Returns:
            WatchdogEvent при проблеме; None — всё в норме или данных мало.
        """
        evt = self._temperature(snapshot)
        if evt is None:
            evt = self._frozen(snapshot)
        if evt is None:
            evt = self._tdr()
        self._last_snap = snapshot
        return evt

    # --- проверки ---

    def _temperature(self, snapshot: TelemetrySnapshot) -> WatchdogEvent | None:
        """Аварийная температура (выше TEMPERATURE_EMERGENCY)."""
        temp = snapshot.gpu_temp_c
        if temp is not None and temp > self.TEMPERATURE_EMERGENCY:
            return WatchdogEvent(
                type="TEMP_EMERGENCY",
                detail=f"температура {temp:.0f}°C > аварийного порога "
                f"{self.TEMPERATURE_EMERGENCY:.0f}°C",
            )
        return None

    def _frozen(self, snapshot: TelemetrySnapshot) -> WatchdogEvent | None:
        """«Заморозка» телеметрии: активность была, энергия не растёт.

        activity_counter стоит между замерами при ненулевой прошлой
        активности, а счётчик энергии не изменился — драйвер, вероятно,
        завис (TDR/перезагрузка улова счётчиков). Простой GPU даёт ту же
        картину честно (activity==0), поэтому проверяется prev>0.
        """
        prev = self._last_snap
        if prev is None:
            return None
        act = (prev.activity_counter, snapshot.activity_counter)
        enj = (prev.gpu_energy_counter_j, snapshot.gpu_energy_counter_j)
        if None in act or None in enj:
            return None  # данных мало — не решаем
        if act[1] > act[0]:  # активность растёт — телеметрия жива
            return None
        if act[0] > 0 and abs(enj[1] - enj[0]) < 1e-6:
            return WatchdogEvent(
                type="TELEMETRY_FROZEN",
                detail="activity_counter стоит, счётчик энергии не растёт — возможен сбой драйвера",
            )
        return None

    def _tdr(self) -> WatchdogEvent | None:
        """TDR после запуска watchdog: Event 4101 в System от старта поиска.

        Returns:
            Событие один раз (повторных TDR-оценок на то же событие нет).
        """
        if self._tdr_found:
            return None
        events = self._query_tdr_events()
        if not events:
            return None
        self._tdr_found = True
        first = events[0]
        return WatchdogEvent(
            type="TDR",
            detail=f"событие {self.TDR_EVENT_ID} ({self.TDR_PROVIDER}) в {first} — "
            "видеодрайвер перестал отвечать и был восстановлен",
        )

    def _query_tdr_events(self) -> list[str]:
        """Ищет свежие TDR-события через Get-WinEvent (время старта = filt).

        Returns:
            Список строк-времени обнаруженных событий (пустой — эффекта нет).
        """
        start = self._started_at.isoformat(timespec="seconds")
        ps = (
            "$ErrorActionPreference='SilentlyContinue';"
            f"$e = Get-WinEvent -FilterHashtable "
            f"@{{LogName='System'; Id=4101; StartTime=[datetime]'{start}'}} | "
            "Sort-Object TimeCreated; "
            "if ($e) { $e | ForEach-Object { $_.TimeCreated.ToString('o') } }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                timeout=10.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Нет PowerShell/таймаут — TDR не проверить: молча (не наш инцидент).
            return []
        out = result.stdout.decode(errors="replace")
        return [line.strip() for line in out.splitlines() if line.strip()]
