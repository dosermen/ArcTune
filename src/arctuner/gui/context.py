"""GpuContext для GUI: реэкспорт из :mod:`arctuner.context`.

Сам контекст больше не зависит от Qt и живёт в ``arctuner/context.py`` —
CLI ``autofind`` использует тот же путь записи/журнала, что и GUI. Этот модуль
оставлен ради существующих импортов (``arctuner.gui.context``), чтобы не
менять вкладки/воркер.
"""

from __future__ import annotations

from arctuner.context import ROLLBACKABLE_FIELDS, DeviceReport, FanInfo, GpuContext

__all__ = ["ROLLBACKABLE_FIELDS", "DeviceReport", "FanInfo", "GpuContext"]
