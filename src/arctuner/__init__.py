"""ArcTune — Python-обёртка над IGCL API для мониторинга и разгона Intel Arc.

Предупреждение: разгон — на ваш страх и риск. См. docs/safety.md.
"""

from __future__ import annotations

import logging

from arctuner.gui.errors import GuiExceptionHandler

__version__ = "0.1.0"
__all__ = ["GuiExceptionHandler", "__version__"]

logger = logging.getLogger(__name__)
