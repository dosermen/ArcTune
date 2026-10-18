"""Низкоуровневая обёртка над IGCL.dll (публичный API Intel).

Слой честно повторяет C API: функции ctl*, структуры _ctl_*_t, enum'ы.
Любая специфика B580 живёт выше — в telemetry/tuner.
"""

from arctuner.igcl.api import IgclError, IgclHandle
from arctuner.igcl.loader import IgclLoader

__all__ = ["IgclError", "IgclHandle", "IgclLoader"]
