"""Watchdog — мониторинг стабильности системы: авторазгон и постоянный режим.

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

Постоянный режим (GUI, вне авторазгона): тот же Watchdog живёт в poll-цикле
воркера — каждое событие пишется в журнал, авто-откат OC включается только
явной опцией (см. arctuner.gui.safety).

Заморозка отфильтровывает простой GPU: на живой машине activity_counter не
возвращается в 0 (стоит на последнем значении), а на погашенном экране
оба счётчика стоят — поэтому инцидент фиксируется только при недавней
нагрузке (см. FROZEN_GROWTH_*), иначе журнал забивался ложными повторами.

При обнаружении проблемы возвращает WatchdogEvent, а не ошибку:
решение (откат, лог, уведомление) — за движком.
"""

from __future__ import annotations

import subprocess
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from arctuner.igcl.api import TelemetrySnapshot

# Запуск из GUI: окно консоли не должно вспыхивать на каждом шаге
# (на не-Windows флага нет — 0, поведение не меняется).
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    TDR_CHECK_INTERVAL: ClassVar[float] = 10.0  # с; PowerShell на каждом шаге дорог
    FROZEN_GROWTH_WINDOW_S: ClassVar[float] = 15.0  # с; окно «недавней нагрузки»
    FROZEN_GROWTH_MIN: ClassVar[int] = 3  # тиков роста activity в окне минимум

    def __init__(self, device: str = "") -> None:
        self._device = device
        self._started_at = datetime.now()
        self._last_snap: TelemetrySnapshot | None = None
        self._tdr_found = False  # однажды найденный TDR не ищется повторно
        self._tdr_last_check: float | None = None  # кэш негатива: не чаще интервала
        self._growth_times: deque[float] = deque()  # моменты роста activity (monotonic)

    def check(self, snapshot: TelemetrySnapshot) -> WatchdogEvent | None:
        """Проверяет снимок. Возвращает первое событие или None.

        Args:
            snapshot: текущий снимок телеметрии (предыдущий следит сам).

        Returns:
            WatchdogEvent при проблеме; None — всё в норме или данных мало.
        """
        self._track_growth(snapshot)
        evt = self._temperature(snapshot)
        if evt is None:
            evt = self._frozen(snapshot)
        if evt is None:
            evt = self._tdr()
        self._last_snap = snapshot
        return evt

    def _track_growth(self, snapshot: TelemetrySnapshot) -> None:
        """Ведёт окно тиков роста activity — «GPU недавно работал».

        Простой/погашенный экран: activity стоит на последнем значении
        (>0, не 0), редкие blips не считаются нагрузкой — без окна
        _frozen принимал бы простой за зависший драйвер.
        """
        now = time.monotonic()
        while self._growth_times and now - self._growth_times[0] > self.FROZEN_GROWTH_WINDOW_S:
            self._growth_times.popleft()
        prev = self._last_snap
        if prev is None:
            return
        cur = snapshot.activity_counter
        if prev.activity_counter is None or cur is None or cur <= prev.activity_counter:
            return
        self._growth_times.append(now)

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
        """«Заморозка» телеметрия: недавняя активность есть, счётчики встали.

        activity_counter стоит между замерами при ненулевой прошлой
        активности, а счётчик энергии не изменился — драйвер, вероятно,
        завис (TDR/перезагрузка улова счётчиков).

        Простой GPU даёт ту же картину счётчиков (оба стоят), при этом
        activity_counter не возвращается в 0 — стоит на последнем значении.
        Поэтому ветка «стоит» требует недавней нагрузки: минимум
        FROZEN_GROWTH_MIN тиков роста activity за FROZEN_GROWTH_WINDOW_S
        (см. _track_growth); редкие blips и погашенный экран — не инцидент.
        """
        prev = self._last_snap
        if prev is None:
            return None
        prev_act = prev.activity_counter
        cur_act = snapshot.activity_counter
        prev_enj = prev.gpu_energy_counter_j
        cur_enj = snapshot.gpu_energy_counter_j
        if prev_act is None or cur_act is None or prev_enj is None or cur_enj is None:
            return None  # данных мало — не решаем
        if cur_act > prev_act:  # активность растёт — телеметрия жива
            return None
        if cur_act < prev_act:
            # Счётчик сброшен/переполнился — после TDR или рестарта драйвера
            # оба счётчика обнуляются (энергия при этом «скачет» — ложная
            # заморозка не детектится, ловим именно сброс активности).
            return WatchdogEvent(
                type="TELEMETRY_FROZEN",
                detail=f"activity_counter сброшен ({prev_act} → {cur_act}) — рестарт драйвера?",
            )
        if prev_act > 0 and abs(cur_enj - prev_enj) < 1e-6:
            if len(self._growth_times) < self.FROZEN_GROWTH_MIN:
                return None  # простой/RC6: GPU давно не работал — не наш инцидент
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
        now = time.monotonic()
        if (
            self._tdr_last_check is not None
            and now - self._tdr_last_check < self.TDR_CHECK_INTERVAL
        ):
            return None  # кэш негатива: PowerShell на каждом шаге дорог
        self._tdr_last_check = now
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
        # Числовой конструктор времени — culture-free: строковый ISO парсится
        # не на всех локалях (не-Григорианские календари ломают [datetime]'…').
        t = self._started_at
        ctor = f"[datetime]::new({t.year},{t.month},{t.day},{t.hour},{t.minute},{t.second})"
        ps = (
            "$ErrorActionPreference='SilentlyContinue';"
            f"$e = Get-WinEvent -FilterHashtable "
            f"@{{LogName='System'; Id={self.TDR_EVENT_ID}; "
            f"ProviderName='{self.TDR_PROVIDER}'; StartTime={ctor}}} | "
            "Sort-Object TimeCreated; "
            "if ($e) { $e | ForEach-Object { $_.TimeCreated.ToString('o') } }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                timeout=10.0,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Нет PowerShell/таймаут — TDR не проверить: молча (не наш инцидент).
            return []
        out = result.stdout.decode(errors="replace")
        return [line.strip() for line in out.splitlines() if line.strip()]
