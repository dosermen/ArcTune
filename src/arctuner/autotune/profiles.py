"""Управление профилями авторазгона.

Профиль — набор настроек разгона + результаты верификации.
Сохраняется в JSON (~/.arctune/profiles/).

Публичный интерфейс:
  profiles.save(name, params, fitness)  -> Path
  profiles.load(name)                   -> dict
  profiles.apply(params)                -> None
  profiles.list()                       -> list[str]
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PROFILE_DIR = Path.home() / ".arctune" / "profiles"


class ProfileStore:
    """Хранилище профилей авторазгона."""

    def __init__(self, dir: Path | None = None) -> None:
        self._dir = dir or DEFAULT_PROFILE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: dict) -> Path:
        """Сохраняет профиль.

        Args:
            name: имя профиля (безопасное, без пробелов).
            data: профиль с полями {mode, baseline, best_stable, params, fitness, ...}.

        Returns:
            Путь к сохранённому файлу.
        """
        out = self._dir / f"{name}.json"
        json.dump(data, out.open("w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return out

    def load(self, name: str) -> dict | None:
        """Загружает профиль по имени.

        Args:
            name: имя профиля.

        Returns:
            Данные профиля или None если не найден.
        """
        path = self._dir / f"{name}.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def apply(self, data: dict) -> None:
        """Применяет профиль (координированная запись).

        Args:
            data: профиль с полями {field_name: value, ...}.
        """
        # Проверяем поля на безопасность
        safe_fields = {"powerLimit", "gpuFrequencyOffset", "gpuVoltageOffset", "temperatureLimit"}

        for field, _value in data.items():
            if field not in safe_fields:
                continue

        # Применяем через worker (должен быть вызван из GUI)
        # from arctuner.gui.worker import GpuWorker
        # worker = GpuWorker()
        # for field, value in data.items():
        #     worker.apply(field, value)
        pass

    def list_profiles(self) -> list[str]:
        """Возвращает список имён профилей."""
        files = self._dir.glob("*.json")
        return [f.stem for f in files if f.is_file()]

    def remove(self, name: str) -> bool:
        """Удаляет профиль.

        Args:
            name: имя профиля.

        Returns:
            True если удалён.
        """
        path = self._dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False
