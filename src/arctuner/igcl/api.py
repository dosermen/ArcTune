"""Python-обёртки над ctl* функциями IGCL + маппинг ошибок.

Каждая функция возвращает dataclass и бросает IgclError при не-успешном коде.
"""

from __future__ import annotations

from dataclasses import dataclass

from arctuner.igcl.loader import IgclLoader
from arctuner.igcl.types import CTL_RESULT_SUCCESS


class IgclError(RuntimeError):
    """Ошибка IGCL (не-успешный _ctl_result_t)."""

    def __init__(self, code: int, func: str) -> None:
        self.code = code
        self.func = func
        super().__init__(f"{func} вернула код ошибки {code}")


@dataclass(frozen=True)
class IgclHandle:
    """Обёртка над _ctl_device_adapter_handle_t."""

    raw: int

    def __bool__(self) -> bool:
        return bool(self.raw)


@dataclass(frozen=True)
class DeviceInfo:
    """Базовые свойства устройства (для поиска B580)."""

    name: str
    vendor_id: int
    device_id: int
    is_integrated: bool


def _check(code: int, func: str) -> None:
    if code != CTL_RESULT_SUCCESS:
        raise IgclError(code, func)


class IgclApi:
    """Точка входа к IGCL. Держит загруженную библиотеку и handle API."""

    def __init__(self, loader: IgclLoader | None = None) -> None:
        self.loader = loader or IgclLoader()
        self._initialized = False

    def initialize(self) -> None:
        """ctlInitialize. TODO: корректная сигнатура (handle API, версия)."""
        raise NotImplementedError(
            "Сигнатура ctlInitialize будет добавлена после сверки с igcl_api.h."
        )

    def shutdown(self) -> None:
        """ctlShutdown."""
        raise NotImplementedError("См. igcl_api.h для сигнатуры ctlShutdown.")

    def enumerate_devices(self) -> list[IgclHandle]:
        """ctlEnumerateDevices — список GPU. TODO."""
        raise NotImplementedError("Сигнатура ctlEnumerateDevices — следующий шаг MVP.")

    def get_device_properties(self, adapter: IgclHandle) -> DeviceInfo:
        """ctlGetDeviceProperties — свойства устройства. TODO."""
        raise NotImplementedError("Следующий шаг MVP: найти B580 по device_id=0xE20B.")

    def read_power_telemetry(self, adapter: IgclHandle) -> dict[str, object]:
        """ctlPowerTelemetryGet — снимок датчиков и причин ограничения. TODO."""
        raise NotImplementedError("Следующий шаг MVP: read-only телеметрия.")
