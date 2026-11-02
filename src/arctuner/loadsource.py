"""Источник данных для загрузки GPU и памяти.

Выбор хранится простым файлом ``~/.arctune/load_source`` (``"igcl"`` / ``"pdh"`` /
``"dxgi"``), а не в QSettings: CLI-exe собирается без PySide6, где Qt недоступен
(см. packaging/arctune.spec). GUI читает и пишет тот же файл, поэтому
переключатель одинаково действует на окно и на командную строку.

Сейчас реализован только ``igcl`` (проверено на живой B580):

- **загрузка ядра %** — ``ctlEngineGetActivity``: %util = ΔactiveTime / Δtimestamp
  двух снимков (формула из igcl_api.h);
- **память (байты/%)** — ``ctlMemoryGetState``: used = size − free,
  % = 100 · used / size.

``pdh`` (``\\GPU Engine(*)\\Utilization Percentage``) и ``dxgi``
(``IDXGIAdapter3::QueryVideoMemoryInfo``) — зарезервированные альтернативные
пути, описанные в docs/igcl-api.md «Альтернативные источники». Выбор любого из
них честно сообщит, что путь ещё не реализован, вместо тихого отката на igcl.
"""

from __future__ import annotations

import os
from pathlib import Path

#: источник из IGCL (реализован, проверен на B580)
SOURCE_IGCL = "igcl"
#: альтернатива: Windows-счётчики производительности (зарезервировано)
SOURCE_PDH = "pdh"
#: альтернатива: DXGI QueryVideoMemoryInfo (зарезервировано)
SOURCE_DXGI = "dxgi"

#: перечень известных источников
SOURCES: tuple[str, ...] = (SOURCE_IGCL, SOURCE_PDH, SOURCE_DXGI)

#: человекочитаемые имена для меню/CLI
SOURCE_LABELS: dict[str, str] = {
    SOURCE_IGCL: "IGCL (ctl*)",
    SOURCE_PDH: "Windows-счётчики (PDH)",
    SOURCE_DXGI: "DXGI (QueryVideoMemoryInfo)",
}

_DEFAULT = SOURCE_IGCL
#: источник текущего процесса (переопределяется set_source, иначе — из файла)
_current: str | None = None


def _source_file() -> Path:
    """Путь к файлу с источником (``~/.arctune/load_source``)."""
    return Path.home() / ".arctune" / "load_source"


def load_source() -> str:
    """Читает сохранённый источник; недоступен/некорректен → igcl.

    Приоритет: переменная окружения ``ARCTUNE_LOAD_SOURCE`` (удобна для тестов и
    переопределения) → файл → источник по умолчанию.

    Returns:
        Код источника (``"igcl"``/``"pdh"``/``"dxgi"``).
    """
    env = os.environ.get("ARCTUNE_LOAD_SOURCE", "").strip().lower()
    if env in SOURCES:
        return env
    try:
        raw = _source_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return _DEFAULT
    return raw if raw in SOURCES else _DEFAULT


def save_source(source: str) -> None:
    """Сохраняет источник в файл (GUI-переключатель).

    Args:
        source: Один из :data:`SOURCES`; прочие значения приводятся к igcl.
    """
    if source not in SOURCES:
        source = _DEFAULT
    path = _source_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def current_source() -> str:
    """Источник текущего процесса (кэшируется после первого чтения)."""
    global _current
    if _current is None:
        _current = load_source()
    return _current


def set_source(source: str) -> None:
    """Явно задаёт источник процесса (используется тестами и переключателем)."""
    global _current
    _current = source if source in SOURCES else _DEFAULT


__all__ = [
    "SOURCES",
    "SOURCE_DXGI",
    "SOURCE_IGCL",
    "SOURCE_LABELS",
    "SOURCE_PDH",
    "current_source",
    "load_source",
    "save_source",
    "set_source",
]
