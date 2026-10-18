"""Загрузка IGCL API и резолв ctl* функций.

Находит библиотеку с публичным IGCL API (функции ctl*) и грузит её через ctypes.
В свежих Arc-драйверах API живёт не в отдельной IGCL.dll, а в ControlLib.dll /
IntelControlLib.dll внутри пакета драйвера (iigd_dch_d — дискретная Arc).
Отсутствующие функции не «глотаются» — явно сообщаются, чтобы старый драйвер
не выглядел поддержанным.
"""

from __future__ import annotations

import ctypes
import glob
import os
from pathlib import Path

# Имена библиотек с IGCL API (ctl*), в порядке приоритета для папки приложения.
_LIB_NAMES = ("IGCL.dll", "IntelControlLib.dll", "ControlLib.dll")

# Каталоги приложения Intel Graphics Software.
_APP_DIRS = (
    r"C:\Program Files\Intel\Intel Graphics Software",
    r"C:\Program Files (x86)\Intel\Intel Graphics Software",
)

# Пакеты дискретного Arc-драйвера в DriverStore (iigd_dch_d = dGPU).
_DRIVERSTORE_GLOB = r"C:\Windows\System32\DriverStore\FileRepository\iigd_dch_d.inf_amd64_*"

# Ключевые функции, которые проекту нужны обязательно.
REQUIRED_FUNCTIONS = (
    "ctlInit",
    "ctlClose",
    "ctlEnumerateDevices",
    "ctlGetDeviceProperties",
    "ctlPowerTelemetryGet",
    "ctlOverclockGetProperties",
    "ctlOverclockResetToDefault",
)


class IgclNotFoundError(RuntimeError):
    """IGCL API не найден или не загрузился."""


class IgclLoader:
    """Грузит библиотеку с IGCL API и резолвит ctl* функции.

    Использование::

        loader = IgclLoader()
        fn = loader.get("ctlEnumerateDevices")
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._dll: ctypes.WinDLL | None = None
        self.path = Path(path) if path else self._find_dll()
        self._load()

    @staticmethod
    def _candidate_paths() -> list[Path]:
        """Кандидаты в порядке приоритета: папка приложения, затем DriverStore."""
        out: list[Path] = []
        for d in _APP_DIRS:
            base = Path(os.path.expandvars(d))
            if not base.exists():
                continue
            for name in _LIB_NAMES:
                out.append(base / name)
        # Дискретный Arc-пакет: ControlLib / IntelControlLib.
        for pkg in sorted(glob.glob(_DRIVERSTORE_GLOB)):
            pkg_path = Path(pkg)
            for name in ("IntelControlLib.dll", "ControlLib.dll"):
                out.append(pkg_path / name)
        return out

    @classmethod
    def _find_dll(cls) -> Path:
        """Первый существующий кандидат. Бросает IgclNotFoundError, если нет."""
        for cand in cls._candidate_paths():
            if cand.exists():
                return cand
        raise IgclNotFoundError(
            "IGCL API не найден. Установите Intel Arc Graphics драйвер (32.0.101.9xxx+): "
            "ожидались IGCL.dll / ControlLib.dll / IntelControlLib.dll в папке приложения "
            "или в DriverStore (пакет iigd_dch_d)."
        )

    def _load(self) -> None:
        try:
            self._dll = ctypes.WinDLL(str(self.path))
        except OSError as e:
            raise IgclNotFoundError(f"Не удалось загрузить: {self.path}") from e

    @property
    def dll(self) -> ctypes.WinDLL:
        if self._dll is None:
            raise IgclNotFoundError("Библиотека не загружена.")
        return self._dll

    def has(self, name: str) -> bool:
        """Есть ли функция в этой версии библиотеки."""
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
        """Сводка для `arctuner doctor`: путь, доступные/отсутствующие функции."""
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "missing_required": self.missing(),
        }
