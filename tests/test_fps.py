"""Проверки PresentMon FPS reader без работающей службы или видеокарты."""

from __future__ import annotations

import ctypes

import pytest

from arctuner.gui import fps as fps_module


class _Function:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakeApi:
    def __init__(self, fps: float = 72.5, poll_status: int = 0) -> None:
        self.fps = fps
        self.poll_status = poll_status
        self.open_calls = 0
        self.track_calls: list[int] = []
        self.close_calls = 0
        self.free_calls = 0
        self.pmOpenSession = _Function(self.open_session)
        self.pmCloseSession = _Function(self.close_session)
        self.pmGetApiVersion = _Function(self.get_api_version)
        self.pmStartTrackingProcess = _Function(self.start_tracking)
        self.pmRegisterDynamicQuery = _Function(self.register_query)
        self.pmPollDynamicQuery = _Function(self.poll_query)
        self.pmFreeDynamicQuery = _Function(self.free_query)

    def open_session(self, out_handle) -> int:
        self.open_calls += 1
        ctypes.cast(out_handle, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1)
        return 0

    @staticmethod
    def close_session(_handle) -> int:
        return 0

    @staticmethod
    def get_api_version(out_version) -> int:
        ctypes.cast(out_version, ctypes.POINTER(fps_module._Version))[0].major = 3
        return 0

    def start_tracking(self, _session, process_id: int) -> int:
        self.track_calls.append(process_id)
        return 0

    @staticmethod
    def register_query(_session, out_query, elements, _count, _window, _offset) -> int:
        ctypes.cast(out_query, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(2)
        elements[0].dataOffset = 8
        elements[0].dataSize = ctypes.sizeof(ctypes.c_double)
        return 0

    def poll_query(self, _query, _process_id, blob_ptr, count_ptr) -> int:
        if self.poll_status:
            return self.poll_status
        blob = ctypes.cast(blob_ptr, ctypes.POINTER(ctypes.c_uint8))
        ctypes.c_double.from_address(ctypes.addressof(blob.contents) + 8).value = self.fps
        ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_uint32))[0] = 1
        return 0

    def free_query(self, _query) -> int:
        self.free_calls += 1
        return 0


def _reader(monkeypatch, api: _FakeApi, process_id: int = 100) -> fps_module.PresentMonFpsReader:
    reader = fps_module.PresentMonFpsReader()
    monkeypatch.setattr(fps_module, "foreground_process_id", lambda: process_id)

    def load_api():
        reader._api = api
        return api

    monkeypatch.setattr(reader, "_load_api", load_api)
    return reader


def test_reader_polls_presented_fps_for_foreground_process(monkeypatch) -> None:
    api = _FakeApi(fps=72.5)
    reader = _reader(monkeypatch, api)

    assert reader.sample() == pytest.approx(72.5)
    assert reader.sample() == pytest.approx(72.5)
    assert api.open_calls == 1
    assert api.track_calls == [100]

    reader.close()
    assert api.free_calls == 1


def test_reader_returns_none_when_no_presented_frames(monkeypatch) -> None:
    reader = _reader(monkeypatch, _FakeApi(fps=0))
    assert reader.sample() is None
    reader.close()


def test_reader_does_not_open_session_without_foreground_process(monkeypatch) -> None:
    reader = fps_module.PresentMonFpsReader()
    monkeypatch.setattr(fps_module, "foreground_process_id", lambda: None)
    monkeypatch.setattr(
        reader, "_load_api", lambda: pytest.fail("API не нужен без активного процесса")
    )

    assert reader.sample() is None


def test_reader_reports_presentmon_service_errors(monkeypatch) -> None:
    reader = _reader(monkeypatch, _FakeApi(poll_status=4))

    with pytest.raises(fps_module.PresentMonError, match="служба недоступна"):
        reader.sample()
