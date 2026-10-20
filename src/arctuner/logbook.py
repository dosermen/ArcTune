"""Журнал изменений настроек + откат к последнему сохранённому состоянию.

Каждая запись из tuner.py фиксируется в JSON-журнале (что было → что стало).
Это даёт две вещи:
  1. Историю — видно, кто/когда/что менял.
  2. Откат — восстановить предыдущие значения одной командой.

Файл по умолчанию: ~/.arctune/logbook.jsonl (append-only, одна запись = один JSON).
Read-only операции сюда не пишутся — только фактические изменения карты.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".arctune"
DEFAULT_LOG_NAME = "logbook.jsonl"


@dataclass
class LogEntry:
    """Одна запись журнала: изменение одного поля."""

    timestamp: str
    field: str
    old: float | None
    new: float | None
    device: str = ""
    note: str = ""
    restored: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class Logbook:
    """Append-only журнал изменений настроек."""

    path: Path = field(default_factory=lambda: DEFAULT_LOG_DIR / DEFAULT_LOG_NAME)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def record(
        self,
        field_name: str,
        old: float | None,
        new: float | None,
        device: str = "",
        note: str = "",
    ) -> LogEntry:
        """Фиксирует изменение (append). Возвращает созданную запись."""
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            field=field_name,
            old=old,
            new=new,
            device=device,
            note=note,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())  # запись переживает внезапное завершение процесса
        return entry

    def mark_restored(self, field_name: str) -> None:
        """Помечает последнюю активную запись поля как восстановленную."""
        entries = self.read_all()
        for e in reversed(entries):
            if e.field == field_name and not e.restored:
                e.restored = True
                break
        self._rewrite(entries)

    def read_all(self) -> list[LogEntry]:
        """Читает все записи (старые → новые)."""
        if not self.path.exists():
            return []
        out: list[LogEntry] = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    out.append(LogEntry(**d))
                except (json.JSONDecodeError, TypeError):
                    continue  # пропускаем битые строки, не роняем чтение
        return out

    def latest_values(self) -> dict[str, float | None]:
        """Последние известные значения полей (для понимания текущего состояния)."""
        vals: dict[str, float | None] = {}
        for e in self.read_all():
            if e.new is not None:
                vals[e.field] = e.new
        return vals

    def rollback_targets(self) -> dict[str, float | None]:
        """Поля → значение «как было» для отката.

        Для каждого поля возвращает самое старое известное `old` (т.е. исходное
        состояние до всех изменений). Поля без history не попадают сюда.
        """
        first_old: dict[str, float | None] = {}
        for e in self.read_all():
            if e.field not in first_old and e.old is not None:
                first_old[e.field] = e.old
        return first_old

    def _rewrite(self, entries: list[LogEntry]) -> None:
        """Атомарно перезаписывает журнал (используется только для mark_restored).

        Пишем во временный файл рядом и подменяем os.replace. Раньше режим "w"
        обнулял журнал до записи: сбой/закрытие приложения посреди перезаписи
        теряло ВСЮ историю. Теперь при ошибке старый файл остаётся целым.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(e.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def tail(self, n: int = 10) -> list[LogEntry]:
        """Последние n записей (новые → старые)."""
        return list(reversed(self.read_all()))[:n]


def default_logbook_path() -> Path:
    """Путь журнала по умолчанию (учитывает ARCTUNE_LOG для тестов/продоки).

    Если ARCTUNE_LOG установлен — расширяем до абсолютного пути,
    но не валидируем расширение (доверенная переменная окружения).
    По умолчанию: ~/.arctune/logbook.jsonl.
    """
    override = os.environ.get("ARCTUNE_LOG")
    if override:
        return Path(override).resolve()
    return DEFAULT_LOG_DIR / DEFAULT_LOG_NAME
