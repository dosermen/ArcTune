"""Вкладка Авторазгон — визуальный интерфейс автопоиска.

Четыре зоны:
  1. Конфигуратор — параметры поиска (режим, шаг, пороги)
  2. Поиск — кнопки старт/стоп, прогресс-бар, лог
  3. Результаты — таблица найденных профилей
  4. Профили — сохранение/загрузка/применение

Зависит от:
  - GpuWorker (для вызова поиска в фоне)
  - autotune.engine (TuningEngine)
  - autotune.profiles (ProfileStore)
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctuner.autofind import MODE_FREQUENCY, MODE_UNDERVOLT
from arctuner.autotune.engine import TuningConfig, TuningResult
from arctuner.autotune.profiles import ProfileStore

# ---------------------------------------------------------------------------


class AutoTuneTab(QWidget):
    """Вкладка авторазгона — конфигуратор + поиск + профили."""

    # Сигналы для связи с GpuWorker
    search_started = Signal()
    search_progress = Signal(int, int, str)  # current, total, text
    search_completed = Signal(object)  # TuningResult
    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: object | None = None  # GpuWorker, тип-хак
        self._profile_store = ProfileStore()
        self._config = TuningConfig()
        self._is_searching = False
        self._build_ui()

    def _build_ui(self) -> None:
        """Строит четыре зоны: конфигуратор | поиск | результаты | профили."""
        # --- Конфигуратор ---
        self._mode_select = QPushButton(f"Режим: {MODE_UNDERVOLT}")
        self._mode_select.clicked.connect(self._on_toggle_mode)
        self._mode_select.setObjectName("infoButton")

        self._step_info = QLabel("Шаг: 2.0")
        self._step_info.setObjectName("infoLabel")

        self._temp_info = QLabel("Темп. потолок: 80 °C")
        self._temp_info.setObjectName("infoLabel")

        # --- Поиск ---
        self._start_btn = QPushButton("▶ Запустить")
        self._start_btn.clicked.connect(self._on_start_search)
        self._start_btn.setObjectName("infoButton")

        self._stop_btn = QPushButton("■ Стоп")
        self._stop_btn.clicked.connect(self._on_stop_search)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setObjectName("dangerButton")

        self._log_area = QLabel("Готово к поиску.")
        self._log_area.setObjectName("logLabel")

        # --- Результаты ---
        self._results_area = QLabel("Результаты: —")
        self._results_area.setObjectName("infoLabel")

        # --- Профили ---
        self._save_profile_btn = QPushButton("Сохранить профиль…")
        self._save_profile_btn.clicked.connect(self._on_save_profile)
        self._save_profile_btn.setObjectName("infoButton")

        self._load_profile_btn = QPushButton("Загрузить профиль…")
        self._load_profile_btn.clicked.connect(self._on_load_profile)
        self._load_profile_btn.setObjectName("infoButton")

        # Собираем в layout
        config_lay = QHBoxLayout()
        config_lay.addWidget(self._mode_select)
        config_lay.addWidget(self._step_info)
        config_lay.addWidget(self._temp_info)

        search_lay = QHBoxLayout()
        search_lay.addWidget(self._start_btn)
        search_lay.addWidget(self._stop_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(config_lay)
        lay.addLayout(search_lay)
        lay.addWidget(self._log_area)
        lay.addWidget(self._results_area)
        lay.addWidget(self._save_profile_btn)
        lay.addWidget(self._load_profile_btn)

    def attach_worker(self, worker: object) -> None:
        """Привязывает к GpuWorker. Вызывается из MainWindow."""
        self._worker = worker

    def _on_toggle_mode(self) -> None:
        """Переключает режим (undervolt / frequency)."""
        current = self._config.mode
        if current == MODE_UNDERVOLT:
            self._config.mode = MODE_FREQUENCY
            self._mode_select.setText(f"Режим: {MODE_FREQUENCY}")
        else:
            self._config.mode = MODE_UNDERVOLT
            self._mode_select.setText(f"Режим: {MODE_UNDERVOLT}")

    def _on_start_search(self) -> None:
        """Запускает поиск в фоне."""
        self._is_searching = True
        self._log_area.setText("Запуск авторазгона…")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self.search_started.emit()

        # Запускаем через worker
        self._worker.autotune_search(self._config)

    def _on_stop_search(self) -> None:
        """Останавливает поиск."""
        self._is_searching = False
        self._log_area.setText("Поиск остановлен.")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def update_progress(self, current: int, total: int, text: str) -> None:
        """Обновляет прогресс-бар и лог."""
        pct = int(current / total * 100)
        self._log_area.setText(f"{pct}% — {text}")

    def display_result(self, result: TuningResult) -> None:
        """Показывает результаты в табличке."""
        lines: list[str] = []
        for entry in result.history:
            lines.append(
                f"  Шаг {entry.get('step', 0):d}: "
                f"value={entry.get('value', 0):.2f} "
                f"temp={entry.get('temp_c', 0):.0f}°C "
                f"fitness={entry.get('fitness', 0):.2f}"
            )
        summary = "\n".join(lines)
        self._results_area.setText(summary)
        self._log_area.setText(f"Готово. Лучшее: {result.best_stable:.2f}")

    def _on_save_profile(self) -> None:
        """Сохраняет текущий лучший результат как профиль."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить профиль авторазгона",
            "",
            "JSON (*.json)",
        )
        if not path:
            return
        data = asdict(self._config)
        self._profile_store.save("unnamed", data)
        self._log_area.setText("Профиль сохранён.")

    def _on_load_profile(self) -> None:
        """Загружает профиль и применяет настройки."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Загрузить профиль авторазгона",
            "",
            "JSON (*.json)",
        )
        if not path:
            return
        p = Path(path)
        data = self._profile_store.load(p.name)
        if not data:
            self._show_error(f"Не удалось загрузить: {p.name}")
            return
        self._config = TuningConfig(**data)
        self._log_area.setText(f"Профиль загружен: {p.name}")

    def set_busy(self, busy: bool) -> None:
        """Блокирует кнопки поиска при работе воркера."""
        self._start_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(bool(busy))
        self._save_profile_btn.setEnabled(not busy)
        self._load_profile_btn.setEnabled(not busy)

    def _show_error(self, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "Авторазгон", text)


__all__ = ["AutoTuneTab"]
