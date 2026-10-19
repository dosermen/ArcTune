"""Вкладка «Авторазгон»: конфигуратор → поиск → результаты → профили.

Прогресс/итоги приходят сигналами GpuWorker (autotune_progress,
autotune_completed); старт/стоп уходят сигналами search_requested/
stop_requested в слоты воркера (все операции с картой — только в его
потоке). Нагрузка, дельта activity_counter, гарды и откат к стабильному
значению — в autotune.engine.
"""

from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctuner.autofind import MODE_FREQUENCY, MODE_UNDERVOLT
from arctuner.autotune.engine import TuningConfig, TuningResult
from arctuner.autotune.profiles import ProfileStore

#: человеческие названия режимов (кнопка-переключатель)
_MODE_LABELS: dict[str, str] = {
    MODE_FREQUENCY: "Частота (+МГц)",
    MODE_UNDERVOLT: "Undervolt (−%)",
}


class AutoTuneTab(QWidget):
    """Конфигурация и запуск адаптивного автопоиска + профили."""

    search_requested = Signal(object)  # TuningConfig → worker.autotune_search
    stop_requested = Signal()  # флаг отмены → worker.autotune_stop

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profiles = ProfileStore()
        self._config = TuningConfig()
        self._build_ui()

    # --- UI ---

    def _build_ui(self) -> None:
        """Строит четыре зоны: конфигуратор | поиск | результат | профили."""
        note = QLabel(
            "Поиск требует нагрузки (запустите бенчмарк/игру; либо задайте нагрузчик "
            "в профиле). Каждый шаг проверяется активностью GPU, температурой и "
            "Watchdog (TDR/сбой телеметрии); при любом сбое карта возвращается к "
            "последнему СТАБИЛЬНОМУ значению. Undervolt смещением на B580 ограничен "
            "драйвером (0…+) — для настоящего undervolt используйте вкладку «V/F»."
        )
        note.setObjectName("hintLabel")
        note.setWordWrap(True)

        self._mode_btn = QPushButton(self._mode_text())
        self._mode_btn.setObjectName("infoButton")
        self._mode_btn.clicked.connect(self._on_toggle_mode)
        self._mode_btn.setToolTip("Переключение режима поиска: частота вверх / undervolt вниз")

        self._start_btn = QPushButton("▶ Запустить поиск")
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setObjectName("infoButton")

        self._stop_btn = QPushButton("■ Стоп")
        # dangerButton — «жёлтохвост»: завершает поиск и откатывает к стабильному.
        self._stop_btn.setObjectName("dangerButton")
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        self._stop_btn.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(self._mode_btn)
        top.addStretch(1)
        top.addWidget(self._start_btn)
        top.addWidget(self._stop_btn)

        self._state = QLabel("Поиск не запускался.")
        self._state.setObjectName("hintLabel")

        self._result = QLabel("Результат: —")
        self._result.setObjectName("resultsLabel")

        save_btn = QPushButton("💾 Сохранить профиль…")
        save_btn.setObjectName("infoButton")
        save_btn.clicked.connect(self._on_save_profile)

        load_btn = QPushButton("📂 Загрузить профиль…")
        load_btn.setObjectName("infoButton")
        load_btn.clicked.connect(self._on_load_profile)

        profiles = QHBoxLayout()
        profiles.addWidget(QLabel("Профили:"))
        profiles.addWidget(save_btn)
        profiles.addWidget(load_btn)
        profiles.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(top)
        lay.addWidget(self._state)
        lay.addWidget(self._result, stretch=1)
        lay.addLayout(profiles)

    def _mode_text(self) -> str:
        return f"Режим: {_MODE_LABELS.get(self._config.mode, self._config.mode)}"

    # --- события UI ---

    def _on_toggle_mode(self) -> None:
        """Переключает режим конфигуратора (конфиг меняется ДО запуска)."""
        self._config.mode = (
            MODE_UNDERVOLT if self._config.mode == MODE_FREQUENCY else MODE_FREQUENCY
        )
        self._mode_btn.setText(self._mode_text())

    def _on_start(self) -> None:
        """Отправляет конфигурацию в воркер (поиск выполняется в его потоке)."""
        self.search_requested.emit(self._config)

    # --- сигналы от worker'а ---

    def update_progress(self, current: int, total: int, text: str) -> None:
        """Живой статус текущего шага (автообновление кнопок — через set_busy)."""
        pct = f"{current}/{total}" if total > 0 else "; текущий шаг"
        self._state.setText(f"Поиск: шаг {pct} — {text}")

    def display_result(self, result: TuningResult) -> None:
        """Итог поиска: лучшее стабильное значение, шаги, причина остановки."""
        self._state.setText(f"Итог: {result.stopped_reason}")
        rows = [self._format_step(e) for e in result.history[-20:]]
        skipped = len(result.history) - len(rows)
        if skipped > 0:
            rows.insert(0, f"… {skipped} ранних шагов не показаны")
        self._result.setText(
            f"Режим {result.mode}: база {result.baseline:g} → лучший стабильный "
            f"{result.best_stable:g} (шагов: {result.steps_taken})\n"
            + ("\n".join(rows) if rows else "шагов не было")
        )

    @staticmethod
    def _format_step(entry: dict) -> str:
        """Строка истории шага; None-датчики не печатаются (не падают)."""
        parts = [f"шаг {entry.get('step', 0)}: {entry.get('value', 0.0):+g}"]
        if entry.get("temp_c") is not None:
            parts.append(f"{entry['temp_c']:.0f}°C")
        if entry.get("power_percent") is not None:
            parts.append(f"{entry['power_percent']:.0f}%")
        if entry.get("fitness") is not None:
            parts.append(f"fitness {entry['fitness']:.2f}")
        return "  " + ", ".join(parts)

    def set_busy(self, busy: bool) -> None:
        """Воркер занят поиском: «Стоп» активно, остальное заблокировано."""
        self._start_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._mode_btn.setEnabled(not busy)

    # --- профили ---

    def _on_save_profile(self) -> None:
        """Сохраняет текущую конфигурацию по выбранному пути (реальному)."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить профиль авторазгона",
            str(self._profiles.dir / "profile.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            saved = self._profiles.save(target, asdict(self._config))
        except OSError as e:
            self._show_error(f"Не удалось сохранить профиль: {e}")
            return
        self._state.setText(f"Профиль сохранён: {saved.name}")

    def _on_load_profile(self) -> None:
        """Загружает профиль из выбранного файла.

        Посторонние ключи JSON игнорируются (к полям TuningConfig не сводятся) —
        чужие/старые файлы не роняют вкладку.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Загрузить профиль авторазгона",
            str(self._profiles.dir),
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            data = self._profiles.load(Path(path))
        except (OSError, ValueError) as e:
            self._show_error(f"Не удалось загрузить профиль: {e}")
            return
        allowed = {f.name for f in fields(TuningConfig)}
        self._config = TuningConfig(**{k: v for k, v in data.items() if k in allowed})
        self._mode_btn.setText(self._mode_text())
        self._state.setText(f"Профиль загружен: {Path(path).name}")

    def _show_error(self, text: str) -> None:
        QMessageBox.warning(self, "Авторазгон", text)


__all__ = ["AutoTuneTab"]
