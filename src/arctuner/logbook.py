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
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_DIR = Path.home() / ".arctune"
DEFAULT_LOG_NAME = "logbook.jsonl"

#: Явный маркер сброса OC к дефолту. Записи ДО него остаются историей, но
#: перестают быть целями отката: карта уже не в том состоянии, куда они вели.
RESET_OC_FIELD = "reset_oc"
#: Явный маркер возврата вентиляторов в авто (история, не цель отката).
RESET_FANS_FIELD = "reset_fans"
#: Маркер сброса из старых версий (field="all"): считаем его сбросом OC.
LEGACY_RESET_OC_FIELD = "all"

#: Поля-маркеры, после которых прежние цели отката OC недействительны.
_RESET_OC_MARKERS = frozenset({RESET_OC_FIELD, LEGACY_RESET_OC_FIELD})


class JournalWriteError(OSError):
    """Запись на карту прошла, но журнал не записался.

    Наследуется от OSError: вызывающие (CLI/GUI) уже показывают такие ошибки
    как проблему файла/диска, но текст сообщает, удалось ли вернуть карту.
    """


def _is_fan_field(name: str) -> bool:
    """Поле журнала относится к вентилятору (`fan[0]`, …)."""
    return name.startswith("fan[")


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


def compute_rollback_targets(entries: Iterable[LogEntry]) -> dict[str, float | None]:
    """Считает поля → значение «как было» по хронологии журнала.

    Это единая семантика для :meth:`Logbook.rollback_targets` и предпросмотра
    GUI: после последнего маркера сброса OC старые записи перестают быть
    целями. Вентиляторы (``fan[*]``) и маркер ``reset_fans`` в цели не входят —
    для них есть отдельная кнопка «Вернуть в авто».

    Поле, чья ПОСЛЕДНЯЯ запись помечена ``restored`` (``log --rollback`` уже
    восстановил его), из целей убирается: повторный откат вернул бы карту к
    тому же значению. Новое изменение поля после восстановления возвращает
    его в цели.

    Args:
        entries: записи журнала в хронологическом порядке (старые → новые).

    Returns:
        Поля → самое старое известное значение ``old`` после последнего сброса.
    """
    targets: dict[str, float | None] = {}
    for entry in entries:
        if entry.field in _RESET_OC_MARKERS:
            targets.clear()
            continue
        if entry.field == RESET_FANS_FIELD or _is_fan_field(entry.field):
            continue
        if entry.restored:
            # Поле восстановлено журналом — цель снимается до нового изменения.
            targets.pop(entry.field, None)
            continue
        if entry.field not in targets and entry.old is not None:
            targets[entry.field] = entry.old
    return targets


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
        """Последние известные значения полей (для понимания текущего состояния).

        Маркер сброса OC очищает прежние OC-значения (карта уже в дефолте),
        маркер ``reset_fans`` — значения вентиляторов. История при этом цела.
        """
        vals: dict[str, float | None] = {}
        for e in self.read_all():
            if e.field in _RESET_OC_MARKERS:
                vals = {k: v for k, v in vals.items() if _is_fan_field(k)}
                continue
            if e.field == RESET_FANS_FIELD:
                vals = {k: v for k, v in vals.items() if not _is_fan_field(k)}
                continue
            if e.new is not None:
                vals[e.field] = e.new
        return vals

    def rollback_targets(self) -> dict[str, float | None]:
        """Поля → значение «как было» для отката.

        Для каждого поля возвращает самое старое известное `old` ПОСЛЕ
        последнего сброса OC: ``reset_oc`` (или legacy ``all``). Записи до
        маркера остаются в истории, но целью отката не являются — иначе после
        ``reset --oc`` журнал снова применил бы старый разгон.

        Вентиляторы журналом не откатываются (для них есть ``reset --fans``),
        поэтому ``fan[*]`` и маркер ``reset_fans`` в цели не попадают.
        """
        return compute_rollback_targets(self.read_all())

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
