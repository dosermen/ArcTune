"""Пакет авторазгона: нагрузчик, движок поиска, watchdog, профили.

Слои:
  1. loader    — стресс-нагрузка (внешний процесс) и проверка активности GPU
  2. engine    — адаптивный спуск с верификацией каждого шага (пишет через
                 GpuContext: tuner-проверки + журнал logbook)
  3. watchdog  — наблюдение: TDR (Event 4101), заморозка телеметрии, авар. t°
  4. profiles  — чтение/запись JSON-профилей конфигурации

Публичный API вкладки: сигналы search_requested/stop_requested → слоты
GpuWorker.autotune_search/autotune_stop; итоги — autotune_progress /
autotune_completed. Все гарантии отката — в engine.py.
"""

from __future__ import annotations

__all__ = ["engine", "loader", "profiles", "watchdog"]
