"""GUI-оболочка ArcTune поверх PySide6 (Qt).

Отделяет жизненный цикл IGCL и логику записи (context.py) от виджетов.
Все write-операции идут через tuner.py (двухуровневые проверки) + журнал —
GUI не реализует собственную запись, только показывает и подтверждает.
"""

from __future__ import annotations

from arctuner.gui.context import FanInfo, GpuContext

__all__ = ["FanInfo", "GpuContext"]
