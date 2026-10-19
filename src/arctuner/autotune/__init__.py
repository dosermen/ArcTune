"""Пакет авторазгона — стресс-нагрузчик, движок поиска, watchdog, профили.

Слои:
  1. loader    — стресс-нагрузка (subprocess, external tool)
  2. engine    — адаптивный бинарный спуск с верификацией
  3. watchdog  — мониторинг TDR / замерзающей телеметрии
  4. profiles  — сохранение / загрузка / применение профилей

Публичный интерфейс:
  autotune.search()        — запускает поиск
  autotune.watchdog()      — проверяет стабильность
  autotune.profiles        — управление профилями
"""

from __future__ import annotations

__all__ = ["engine", "loader", "profiles", "watchdog"]
