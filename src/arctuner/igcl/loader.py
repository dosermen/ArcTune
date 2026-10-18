"""Загрузка IGCL.dll и резолв ctl* функций.

Ищет IGCL.dll в стандартных путях Intel-драйвера, грузит через ctypes.WinDLL
и отдаёт словарь доступных функций. Отсутствующие функции не «глотает» —
явно сообщает, чтобы старый драйвер не выглядел поддержанным.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

# Кандидаты путей к IGCL.dll (проверяются по порядку).
_CANDIDATE_DIRS = [
    r"C:\Program Files\Intel\Intel Graphics Software",
    r"C:\Program Files (x86)\Intel\Intel Graphics Software",
    os.path.expandvars(r"%ProgramData%\Intel"),
]

# Ключевые функции, которые проекту нужны обязательно.
REQUIRED_FUNCTIONS = (
    "ctlInitialize",
    "ctlShutdown",
    "ctlEnumerateDevices",
    "ctlGetDeviceProperties",
    "ctlPowerTelemetryGet",
    "ctlOverclockGetProperties",
    "ctlOverclockResetToDefault",
)


class IgclNotFoundError(RuntimeError):
    """IGCL.dll не найдена или не загрузилась."""


class IgclLoader:
    """Грузит IGCL.dll и резолвит ctl* функции.

    Использование::

        loader = IgclLoader()
        fn = loader.get("ctlEnumerateDevices")
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._dll: ctypes.WinDLL | None = None
        self.path = Path(path) if path else self._find_dll()
        self._load()

    @staticmethod
    def _find_dll() -> Path:
        """Ищет IGCL.dll в известных каталогах Intel."""
        for d in _CANDIDATE_DIRS:
            base = Path(os.path.expandvars(d))
            if not base.exists():
                continue
            # Приоритет — 64-битная версия.
            for name in ("IGCL.dll", "igcl64.dll", "igcl32.dll"):
                cand = base / name
                if cand.exists():
                    return cand
            # Иногда DLL лежит в подпапке.
            found = list(base.glob("**/IGCL.dll"))
            if found:
                return found[0]
        raise IgclNotFoundError(
            "IGCL.dll не найдена. Установите Intel Arc Graphics драйвер "
            "(32.0.101.9xxx+)."
        )

    def _load(self) -> None:
        try:
            self._dll = ctypes.WinDLL(str(self.path))
        except OSError as e:
            raise IgclNotFoundError(f"Не удалось загрузить IGCL.dll: {self.path}") from e

    @property
    def dll(self) -> ctypes.WinDLL:
        if self._dll is None:
            raise IgclNotFoundError("IGCL.dll не загружена.")
        return self._dll

    def has(self, name: str) -> bool:
        """Есть ли функция в этой версии IGCL.dll."""
        try:
            getattr(self.dll, name)
            return True
        except AttributeError:
            return False

    def get(self, name: str):
        """Возвращает ctl*-функцию. Бросает AttributeError, если её нет."""
        return getattr(self.dll, name)

    def missing(self, names: tuple[str, ...] = REQUIRED_FUNCTIONS) -> list[str]:
        """Список функций, отсутствующих в данной версии драйвера."""
        return [n for n in names if not self.has(n)]

    def healthcheck(self) -> dict[str, object]:
        """Сводка для `arctuner doctor`: путь, версия, доступные/отсутствующие функции."""
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "missing_required": self.missing(),
        }
