"""Чтение FPS активного приложения через публичный PresentMon API 2."""

from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
from typing import Any

_PM_STATUS_SUCCESS = 0
_PM_METRIC_PRESENTED_FPS = 12
_PM_STAT_AVG = 1
_MAX_SWAP_CHAINS = 16
_WINDOW_MS = 1000.0
_DLL_NAME = "PresentMonAPI2.dll"


class PresentMonError(RuntimeError):
    """Ошибка подключения к PresentMon или чтения его метрик."""


class _QueryElement(ctypes.Structure):
    _fields_ = [
        ("metric", ctypes.c_int),
        ("stat", ctypes.c_int),
        ("deviceId", ctypes.c_uint32),
        ("arrayIndex", ctypes.c_uint32),
        ("dataOffset", ctypes.c_uint64),
        ("dataSize", ctypes.c_uint64),
    ]


class _Version(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint16),
        ("minor", ctypes.c_uint16),
        ("patch", ctypes.c_uint16),
        ("tag", ctypes.c_char * 22),
        ("hash", ctypes.c_char * 8),
        ("config", ctypes.c_char * 4),
    ]


def foreground_process_id() -> int | None:
    """Возвращает PID процесса активного окна Windows."""
    if os.name != "nt":
        return None

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetForegroundWindow.argtypes = ()
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
    user32.GetWindowThreadProcessId.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    )
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    process_id = ctypes.c_uint32()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)):
        return None
    return process_id.value or None


class PresentMonFpsReader:
    """Читает средний FPS foreground-приложения за последнее окно в 1 секунду.

    PresentMon service/API отсутствует на части систем или требует членства
    пользователя в Performance Log Users. В этом случае ``sample`` сообщает
    ошибку, а вызывающий отображает FPS как недоступный.
    """

    def __init__(self) -> None:
        self._api: Any = None
        self._dll_directory: object | None = None
        self._session: ctypes.c_void_p | None = None
        self._query: ctypes.c_void_p | None = None
        self._tracked_process_id: int | None = None
        self._blob_size = 0
        self._fps_offset = 0

    def sample(self) -> float | None:
        """Возвращает FPS активного приложения; без окна/кадров — ``None``."""
        process_id = foreground_process_id()
        if process_id is None:
            self.close()
            return None
        if process_id != self._tracked_process_id:
            self.close()
            self._open_session(process_id)

        assert self._api is not None
        assert self._query is not None
        blob = ctypes.create_string_buffer(self._blob_size * _MAX_SWAP_CHAINS)
        swap_chain_count = ctypes.c_uint32(_MAX_SWAP_CHAINS)
        status = self._api.pmPollDynamicQuery(
            self._query,
            process_id,
            ctypes.cast(blob, ctypes.POINTER(ctypes.c_uint8)),
            ctypes.byref(swap_chain_count),
        )
        if status != _PM_STATUS_SUCCESS:
            error = self._status_error("опрос FPS", status)
            self._close_after_error(error)
        if swap_chain_count.value > _MAX_SWAP_CHAINS:
            error = PresentMonError("PresentMon вернул некорректное число swap chain.")
            self._close_after_error(error)

        values = (
            ctypes.c_double.from_buffer(blob, index * self._blob_size + self._fps_offset).value
            for index in range(swap_chain_count.value)
        )
        positive_values = [value for value in values if math.isfinite(value) and value > 0]
        return max(positive_values, default=None)

    def close(self) -> None:
        """Освобождает query и сессию PresentMon."""
        api = self._api
        query = self._query
        session = self._session
        self._query = None
        self._session = None
        self._tracked_process_id = None
        self._blob_size = 0
        self._fps_offset = 0
        if api is None:
            return

        errors: list[str] = []
        if query is not None:
            status = api.pmFreeDynamicQuery(query)
            if status != _PM_STATUS_SUCCESS:
                errors.append(self._status_error("освобождение FPS query", status).args[0])
        if session is not None:
            status = api.pmCloseSession(session)
            if status != _PM_STATUS_SUCCESS:
                errors.append(self._status_error("закрытие FPS session", status).args[0])
        if errors:
            raise PresentMonError("; ".join(errors))

    def _open_session(self, process_id: int) -> None:
        api = self._load_api()
        session = ctypes.c_void_p()
        status = api.pmOpenSession(ctypes.byref(session))
        if status != _PM_STATUS_SUCCESS:
            raise self._status_error("подключение к службе PresentMon", status)
        if not session.value:
            raise PresentMonError("PresentMon вернул пустой session handle.")
        self._session = session

        version = _Version()
        status = api.pmGetApiVersion(ctypes.byref(version))
        if status != _PM_STATUS_SUCCESS:
            self._close_after_error(self._status_error("проверка версии PresentMon API", status))
        if version.major < 3:
            self._close_after_error(
                PresentMonError(
                    f"PresentMon API {version.major}.{version.minor} не поддерживается "
                    "(требуется версия 3 или новее)."
                )
            )

        element = _QueryElement(
            _PM_METRIC_PRESENTED_FPS,
            _PM_STAT_AVG,
            0,
            0,
            0,
            0,
        )
        elements = (_QueryElement * 1)(element)
        query = ctypes.c_void_p()
        status = api.pmRegisterDynamicQuery(
            session,
            ctypes.byref(query),
            elements,
            1,
            _WINDOW_MS,
            0.0,
        )
        if status != _PM_STATUS_SUCCESS:
            self._close_after_error(self._status_error("регистрация FPS query", status))
        self._query = query
        if not query.value or elements[0].dataSize != ctypes.sizeof(ctypes.c_double):
            self._close_after_error(
                PresentMonError("PresentMon вернул неизвестный формат метрики FPS.")
            )

        self._blob_size = int(elements[0].dataOffset + elements[0].dataSize)
        self._fps_offset = int(elements[0].dataOffset)
        status = api.pmStartTrackingProcess(session, process_id)
        if status != _PM_STATUS_SUCCESS:
            self._close_after_error(self._status_error("запуск отслеживания FPS", status))
        self._tracked_process_id = process_id

    def _load_api(self) -> Any:
        if self._api is not None:
            return self._api

        path = self._find_api_path()
        try:
            self._dll_directory = os.add_dll_directory(str(path.parent))
            api = ctypes.WinDLL(str(path), use_last_error=True)
        except OSError as exc:
            raise PresentMonError(f"не удалось загрузить {path}: {exc}") from exc

        self._bind_api(api)
        self._api = api
        return api

    @staticmethod
    def _find_api_path() -> Path:
        configured = os.environ.get("ARCTUNE_PRESENTMON_API2")
        if configured:
            path = Path(os.path.expandvars(configured)).expanduser()
            if not path.is_file():
                raise PresentMonError(
                    f"ARCTUNE_PRESENTMON_API2 указывает на отсутствующий файл: {path}"
                )
            return path

        roots = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        ]
        candidates = [root / "Intel" / "Intel Graphics Software" / _DLL_NAME for root in roots] + [
            root / "Intel" / "PresentMonSharedService" / _DLL_NAME for root in roots
        ]
        found_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if found_path is None:
            raise PresentMonError(
                f"{_DLL_NAME} не найдена. Установите Intel Graphics Software/PresentMon "
                "или задайте путь в ARCTUNE_PRESENTMON_API2."
            )
        return found_path

    @staticmethod
    def _bind_api(api: Any) -> None:
        api.pmOpenSession.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
        api.pmOpenSession.restype = ctypes.c_int
        api.pmCloseSession.argtypes = (ctypes.c_void_p,)
        api.pmCloseSession.restype = ctypes.c_int
        api.pmGetApiVersion.argtypes = (ctypes.POINTER(_Version),)
        api.pmGetApiVersion.restype = ctypes.c_int
        api.pmStartTrackingProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        api.pmStartTrackingProcess.restype = ctypes.c_int
        api.pmRegisterDynamicQuery.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(_QueryElement),
            ctypes.c_uint64,
            ctypes.c_double,
            ctypes.c_double,
        )
        api.pmRegisterDynamicQuery.restype = ctypes.c_int
        api.pmPollDynamicQuery.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint32),
        )
        api.pmPollDynamicQuery.restype = ctypes.c_int
        api.pmFreeDynamicQuery.argtypes = (ctypes.c_void_p,)
        api.pmFreeDynamicQuery.restype = ctypes.c_int

    def _close_after_error(self, error: PresentMonError) -> None:
        try:
            self.close()
        except PresentMonError as cleanup_error:
            raise PresentMonError(f"{error}; {cleanup_error}") from error
        raise error

    @staticmethod
    def _status_error(operation: str, status: int) -> PresentMonError:
        descriptions = {
            1: "общая ошибка",
            2: "неверный аргумент",
            3: "неверный handle",
            4: "служба недоступна",
            6: "PID недействителен",
            12: "ошибка канала связи",
            13: "сессия не открыта",
        }
        description = descriptions.get(status, "неизвестная ошибка")
        extra = (
            " Проверьте, что служба PresentMon запущена и пользователь входит "
            "в группу Performance Log Users."
            if status in {4, 12}
            else ""
        )
        return PresentMonError(f"{operation}: {description} (код {status}).{extra}")


__all__ = ["PresentMonError", "PresentMonFpsReader", "foreground_process_id"]
