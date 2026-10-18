"""Тесты layout структур igcl/types.py.

ВАЖНО: размеры/смещения должны совпадать с igcl_api.h вашей версии драйвера.
Эти тесты ловят опечатки в types.py на этапе, когда реального Arc ещё нет.
"""

from __future__ import annotations

import ctypes

from arctuner.igcl.types import CtlVoltageFrequencyPoint


def test_vf_point_size() -> None:
    """_ctl_voltage_frequency_point_t: два uint32 → ровно 8 байт."""
    assert ctypes.sizeof(CtlVoltageFrequencyPoint) == 8


def test_vf_point_offsets() -> None:
    """voltage идёт первым (offset 0), frequency — вторым (offset 4)."""
    assert CtlVoltageFrequencyPoint.voltage.offset == 0
    assert CtlVoltageFrequencyPoint.frequency.offset == 4
