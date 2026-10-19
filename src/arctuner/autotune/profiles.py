"""Профили авторазгона: чтение/запись JSON-файлов.

Профиль — сериализованный TuningConfig (dict). Хранение — по выбранному
пользователем пути (диалог GUI или явный Path); Каталог по умолчанию
(~/.arctune/profiles/) используется только как стартовая папка диалога.

Здесь НЕТ применения профиля к карте: применение — запись через
GpuContext с подтверждением в UI. Заглушки-применения специально нет —
чтобы не создавать видимость безопасности.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PROFILE_DIR = Path.home() / ".arctune" / "profiles"


class ProfileStore:
    """Чтение и запись профилей авторазгона (JSON)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or DEFAULT_PROFILE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def dir(self) -> Path:
        """Каталог профилей по умолчанию (стартовая папка диалога)."""
        return self._dir

    def save(self, path: Path, data: dict) -> Path:
        """Сохраняет профиль по указанному пути.

        Args:
            path: целевой файл (расширение .json на совести вызывающего).
            data: сериализованный TuningConfig (dict простых типов).

        Returns:
            Путь, по которому записан профиль.

        Raises:
            OSError: файл/каталог не создаётся.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return out

    def load(self, path: Path) -> dict:
        """Читает профиль по указанному пути.

        Args:
            path: файл профиля.

        Returns:
            Содержимое профиля (dict).

        Raises:
            FileNotFoundError: файла нет.
            ValueError: JSON повреждён или это не словарь.
            OSError: файл не читается.
        """
        with Path(path).open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"профиль {path} — не словарь")
        return data


__all__ = ["DEFAULT_PROFILE_DIR", "ProfileStore"]
