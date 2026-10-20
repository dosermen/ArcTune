"""Реплей инцидента: кольцевой буфер телеметрии + дамп при сбое (read-only).

Отвечает на вечный вопрос после вылета драйвера/TDR/фриза: «что было прямо
перед смертью карты?». Пока карта живёт — poll-цикл толкает снимки в буфер;
при обнаружении инцидента (сброс счётчика энергии = драйвер перезагрузился)
или по Ctrl+C последние N минут дампятся в JSONL.

Дамп фиксирует ТОЛЬКО факты (данные + триггер), причину не диагностирует —
это работа человека или watchdog. Read-only по построению.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from arctuner.igcl.api import TelemetrySnapshot

DEFAULT_REPLAY_DIR = Path.home() / ".arctune" / "incidents"
DEFAULT_MAX_SAMPLES = 600  # при 1 Гц — 10 минут контекста
DEFAULT_KEEP_FILES = 20  # ротация: не копить дампы бесконечно


@dataclass(frozen=True)
class IncidentMeta:
    """Первая строка дампа: что случилось, когда и на чём."""

    trigger: str  # «energy_counter_reset» / «manual_interrupt» / …
    dumped_at: str  # ISO 8601 UTC
    started_at: str  # ISO 8601 UTC — момент первого снимка в буфере
    samples: int
    device: str


class IncidentReplay:
    """Кольцевой буфер снимков телеметрии + JSONL-дамп инцидента.

    Использование (poll-цикл)::

        replay = IncidentReplay(device=info.name)
        while polling:
            snap = api.read_power_telemetry(handle)
            trigger = replay.push(snap)
            if trigger:
                path = replay.dump(trigger)   # драйвер перезагрузился

    Один экземпляр на один поток опроса (не потокобезопасен).
    """

    def __init__(
        self,
        max_samples: int = DEFAULT_MAX_SAMPLES,
        directory: Path | None = None,
        keep_files: int = DEFAULT_KEEP_FILES,
        device: str = "",
    ) -> None:
        self._buf: deque[tuple[float, float, TelemetrySnapshot]] = deque(maxlen=max_samples)
        self._dir = Path(directory) if directory else DEFAULT_REPLAY_DIR
        self._keep_files = keep_files
        self._device = device
        # Предыдущие значения счётчиков энергии для детекта сброса (TDR/ребут
        # драйвера обнуляют их — PowerMeter это уже ловит как перебазу).
        self._prev_energy: dict[str, float] = {}

    def push(
        self,
        snap: TelemetrySnapshot,
        t: float | None = None,
        wall: float | None = None,
    ) -> str | None:
        """Добавляет снимок; возвращает триггер, если обнаружен инцидент.

        Args:
            snap: снимок телеметрии от read_power_telemetry.
            t: monotonic-метка (в тестах передаётся явно; по умолчанию — сейчас).
            wall: epoch-метка для дампа (по умолчанию time.time()).
        """
        now_mono = time.monotonic() if t is None else t
        trigger = self._detect_reset(snap)
        self._buf.append((now_mono, time.time() if wall is None else wall, snap))
        return trigger

    def _detect_reset(self, snap: TelemetrySnapshot) -> str | None:
        """Сравнивает счётчики энергии с предыдущим снимком.

        База обновляется для ВСЕХ трёх счётчиков даже после найденного сброса:
        иначе card/vram «падают» на следующем снимке и дают ещё два пустых
        дампа на один инцидент.
        """
        trigger: str | None = None
        for key, value in (
            ("gpu", snap.gpu_energy_counter_j),
            ("card", snap.card_energy_counter_j),
            ("vram", snap.vram_energy_counter_j),
        ):
            if value is None:
                continue
            prev = self._prev_energy.get(key)
            self._prev_energy[key] = value
            if prev is not None and value < prev and trigger is None:
                trigger = f"energy_counter_reset_{key}"
        return trigger

    def dump(self, trigger: str) -> Path | None:
        """Пишет буфер в JSONL и очищает его. None — буфер пуст, dump нечестен.

        Первая строка — IncidentMeta, далее строки-снапшоты с двумя метками
        времени: t (секунды от начала буфера, monotonic) и wall (epoch).

        Запись атомарна: данные идут во временный файл в том же каталоге,
        затем os.replace. Читатель не увидит обрезанный дамп, а сбой записи
        (диск/права/fsync) оставляет буфер нетронутым — данные не теряются и
        дамп можно повторить. Исключение не глотается, временный файл удаляется.
        """
        if not self._buf:
            return None
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._unique_path(trigger)
        tmp = path.with_name(path.name + ".tmp")
        t0_mono = self._buf[0][0]
        started = datetime.fromtimestamp(self._buf[0][1], tz=timezone.utc).isoformat(
            timespec="seconds"
        )
        meta = IncidentMeta(
            trigger=trigger,
            dumped_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            started_at=started,
            samples=len(self._buf),
            device=self._device,
        )
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps({"meta": asdict(meta)}, ensure_ascii=False) + "\n")
                for mono, wall, snap in self._buf:
                    f.write(
                        json.dumps(
                            {"t": round(mono - t0_mono, 3), "wall": wall, **asdict(snap)},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                f.flush()
                os.fsync(f.fileno())  # данные на диске до подмены имени
            os.replace(tmp, path)
        except BaseException:
            # Буфер НЕ очищаем: вызывающий может повторить дамп без потери
            # контекста инцидента. Сбой уборки не должен скрыть исходную ошибку.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        self._buf.clear()
        self._rotate()
        return path

    def _unique_path(self, trigger: str) -> Path:
        """Свободное имя дампа: микросекунды + pid, при совпадении — суффикс.

        Секундной метки не хватало: два инцидента подряд (или повторный дамп
        в ту же секунду) перезаписывали один и тот же файл.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        base = f"incident_{stamp}_{os.getpid()}_{trigger}"
        path = self._dir / f"{base}.jsonl"
        suffix = 1
        while path.exists():
            path = self._dir / f"{base}_{suffix}.jsonl"
            suffix += 1
        return path

    def _rotate(self) -> None:
        """Оставляет последние keep_files дампов (имя = метка времени)."""
        dumps = sorted(self._dir.glob("incident_*.jsonl"))
        for old in dumps[: max(0, len(dumps) - self._keep_files)]:
            old.unlink(missing_ok=True)

    @property
    def samples(self) -> int:
        """Сколько снимков сейчас в буфере (для статуса в poll-цикле)."""
        return len(self._buf)


__all__ = [
    "DEFAULT_KEEP_FILES",
    "DEFAULT_MAX_SAMPLES",
    "DEFAULT_REPLAY_DIR",
    "IncidentMeta",
    "IncidentReplay",
    "default_incidents_dir",
]


def default_incidents_dir() -> Path:
    """Директория дампов по умолчанию (~/.arctune/incidents)."""
    return DEFAULT_REPLAY_DIR
