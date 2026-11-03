"""Вкладки главного окна GUI."""

from __future__ import annotations

from arctuner.gui.tabs.autotune import AutoTuneTab
from arctuner.gui.tabs.device import DeviceTab
from arctuner.gui.tabs.fans import FansTab
from arctuner.gui.tabs.logtab import LogTab
from arctuner.gui.tabs.monitoring import MonitoringTab
from arctuner.gui.tabs.overclock import OverclockTab
from arctuner.gui.tabs.overlay import OverlayTab
from arctuner.gui.tabs.vf import VfTab

__all__ = [
    "AutoTuneTab",
    "DeviceTab",
    "FansTab",
    "LogTab",
    "MonitoringTab",
    "OverclockTab",
    "OverlayTab",
    "VfTab",
]
