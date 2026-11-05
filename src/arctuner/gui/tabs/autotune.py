"""Вкладка «Авторазгон»: конфигуратор → поиск → результаты → профили.

Прогресс/итоги приходят сигналами GpuWorker (autotune_progress,
autotune_completed); старт/стоп уходят сигналами search_requested/
stop_requested в слоты воркера (все операции с картой — только в его
потоке). Нагрузка, дельта activity_counter, гарды и откат к стабильному
значению — в autotune.engine.
"""

from __future__ import annotations

import time
from dataclasses import asdict, fields
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arctuner.autotune.engine import (
    MODE_FREQUENCY,
    MODE_UNDERVOLT,
    TuningConfig,
    TuningResult,
)
from arctuner.autotune.profiles import ProfileStore
from arctuner.gui.charts import LiveChart
from arctuner.i18n import tr
from arctuner.igcl.api import TelemetrySnapshot

#: человеческие названия режимов (кнопка-переключатель)
_MODE_LABELS: dict[str, str] = {
    MODE_FREQUENCY: "Частота (+МГц)",
    MODE_UNDERVOLT: "Undervolt (−%)",
}
_MODE_LABELS_EN: dict[str, str] = {
    MODE_FREQUENCY: "Frequency (+MHz)",
    MODE_UNDERVOLT: "Undervolt (−%)",
}

#: пауза дольше этого — пропуск данных: линию через неё не рисуем (не врём).
_GAP_MAX_S = 5.0


class AutoTuneTab(QWidget):
    """Конфигурация и запуск адаптивного автопоиска + профили."""

    search_requested = Signal(object)  # TuningConfig → worker.autotune_search
    stop_requested = Signal()  # флаг отмены → worker.autotune_stop

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profiles = ProfileStore()
        self._config = TuningConfig()
        self._t0 = time.monotonic()
        self._t_last: float | None = None
        self._build_ui()

    # --- UI ---

    def _build_ui(self) -> None:
        """Строит четыре зоны: конфигуратор | поиск | результат | профили."""
        note = QLabel(
            tr(
                "Поиск требует нагрузки (запустите бенчмарк/игру; либо задайте нагрузчик "
                "в профиле). Каждый шаг проверяется активностью GPU, температурой и "
                "Watchdog (TDR/сбой телеметрии); при любом сбое карта возвращается к "
                "последнему СТАБИЛЬНОМУ значению. Undervolt смещением на B580 ограничен "
                "драйвером (0…+) — для настоящего undervolt используйте вкладку «V/F».",
                "The search requires a workload (run a benchmark/game or configure a load "
                "command in the profile). Each step is checked for GPU activity, temperature, "
                "and watchdog events (TDR/telemetry failure); on failure, the card returns "
                "to the last STABLE value. B580 voltage offset is driver-limited (0…+); use "
                "the V/F tab for actual undervolting.",
            )
        )
        note.setObjectName("hintLabel")
        note.setWordWrap(True)

        self._mode_btn = QPushButton(self._mode_text())
        self._mode_btn.setObjectName("infoButton")
        self._mode_btn.clicked.connect(self._on_toggle_mode)
        self._mode_btn.setToolTip(
            tr(
                "Переключение режима поиска: частота вверх / undervolt вниз",
                "Switch search mode: increase frequency / decrease voltage offset",
            )
        )

        self._start_btn = QPushButton(tr("▶ Запустить поиск", "▶ Start search"))
        self._start_btn.clicked.connect(self._on_start)
        self._start_btn.setObjectName("infoButton")

        self._stop_btn = QPushButton(tr("■ Стоп", "■ Stop"))
        # dangerButton — «жёлтохвост»: завершает поиск и откатывает к стабильному.
        self._stop_btn.setObjectName("dangerButton")
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        self._stop_btn.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(self._mode_btn)
        top.addStretch(1)
        top.addWidget(self._start_btn)
        top.addWidget(self._stop_btn)

        self._state = QLabel(tr("Поиск не запускался.", "Search has not been started."))
        self._state.setObjectName("hintLabel")

        self._now = QLabel(tr("Сейчас: —", "Current: —"))
        self._now.setObjectName("hintLabel")

        self._result = QLabel(tr("Результат: —", "Result: —"))
        self._result.setObjectName("resultsLabel")
        self._result.setWordWrap(True)
        self._result.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # История шагов может быть длинной (до 20 строк) — пусть прокручивается,
        # а не растягивает вкладку и не обрезается.
        result_scroll = QScrollArea()
        result_scroll.setWidgetResizable(True)
        result_scroll.setFrameShape(QFrame.Shape.NoFrame)
        result_scroll.setWidget(self._result)

        self._live = LiveChart(window_s=120)
        self._live.setMaximumHeight(300)
        live_caption = QLabel(
            tr(
                "Живая телеметрия поиска (°C / % слева, МГц справа, окно 2 мин):",
                "Live search telemetry (°C / % left, MHz right, 2-minute window):",
            )
        )
        live_caption.setObjectName("hintLabel")

        self._save_btn = QPushButton(tr("💾 Сохранить профиль…", "💾 Save profile…"))
        self._save_btn.setObjectName("infoButton")
        self._save_btn.clicked.connect(self._on_save_profile)

        self._load_btn = QPushButton(tr("📂 Загрузить профиль…", "📂 Load profile…"))
        self._load_btn.setObjectName("infoButton")
        self._load_btn.clicked.connect(self._on_load_profile)

        profiles = QHBoxLayout()
        profiles.addWidget(QLabel(tr("Профили:", "Profiles:")))
        profiles.addWidget(self._save_btn)
        profiles.addWidget(self._load_btn)
        profiles.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addLayout(top)
        lay.addWidget(self._state)
        lay.addWidget(self._now)
        lay.addWidget(live_caption)
        lay.addWidget(self._live)  # высота ограничена: история под ним не задавлена
        lay.addWidget(result_scroll, stretch=1)
        lay.addLayout(profiles)

    def _mode_text(self) -> str:
        ru = _MODE_LABELS.get(self._config.mode, self._config.mode)
        en = _MODE_LABELS_EN.get(self._config.mode, self._config.mode)
        return tr(f"Режим: {ru}", f"Mode: {en}")

    # --- события UI ---

    def _on_toggle_mode(self) -> None:
        """Переключает режим конфигуратора (конфиг меняется ДО запуска)."""
        self._config.mode = (
            MODE_UNDERVOLT if self._config.mode == MODE_FREQUENCY else MODE_FREQUENCY
        )
        self._mode_btn.setText(self._mode_text())

    def _on_start(self) -> None:
        """Отправляет конфигурацию в воркер (поиск выполняется в его потоке).

        Кнопки гасятся ДО emit: busy_changed из воркера едет через очередь и
        окно «клик записи» успело бы пролезть, пока поиск не начался. Команда
        нагрузчика из профиля исполняется как есть — показываем её и просим
        подтверждение (профиль — обычный JSON, чужой файл не должен молча
        выполнять код).
        """
        if self._config.external_cmd:
            answer = QMessageBox.question(
                self,
                tr("ArcTune — нагрузчик из профиля", "ArcTune — profile workload command"),
                tr(
                    f"Будет запущена команда из профиля:\n{self._config.external_cmd}\n\nПродолжить?",
                    f"This profile command will be run:\n{self._config.external_cmd}\n\nContinue?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._state.setText(
                    tr(
                        "Запуск отменён (нагрузчик из профиля не подтверждён)",
                        "Start cancelled (profile workload command was not confirmed)",
                    )
                )
                return
        # Новый поиск — новый график: старые точки к нему не относятся.
        self._t0 = time.monotonic()
        self._t_last = None
        self._live.clear()
        self._now.setText(
            tr("Сейчас: ожидание первого снимка…", "Current: waiting for first sample…")
        )
        self.set_busy(True)  # локально и сразу; повторно воркер пришлёт тот же статус
        self._state.setText(
            tr(
                "Запуск: подготовка поиска… (потребуется нагрузка на GPU, см. примечание выше)",
                "Starting: preparing search… (GPU load is required; see the note above)",
            )
        )
        self.search_requested.emit(self._config)

    # --- сигналы от worker'а ---

    def update_progress(self, current: int, total: int, text: str) -> None:
        """Живой статус: этапы (current=0) и успешные шаги (current=номер)."""
        if current > 0 and total > 0:
            self._state.setText(
                tr(
                    f"Поиск: шаг {current}/{total} — {text}",
                    f"Search: step {current}/{total} — {text}",
                )
            )
        else:
            self._state.setText(tr(f"Поиск: {text}", f"Search: {text}"))

    def on_sample(self, snap: TelemetrySnapshot) -> None:
        """Живой снимок поиска: мини-график и строка «сейчас».

        Приходит только во время поиска (autotune_sample воркера), на каждом
        чтении телеметрии движком — включая паузы settle и ожидание нагрузки.
        """
        freq = snap.gpu_freq_mhz
        temp = snap.gpu_temp_c
        power = snap.gpu_power_percent
        parts: list[str] = []
        if freq is not None:
            parts.append(f"{freq:.0f} МГц")
        if temp is not None:
            parts.append(f"{temp:.0f} °C")
        if power is not None:
            parts.append(f"{power:.0f} %")
        if snap.gpu_power_w is not None:
            parts.append(f"{snap.gpu_power_w:.0f} Вт")
        if any(
            (
                snap.power_limited,
                snap.temperature_limited,
                snap.current_limited,
                snap.voltage_limited,
                snap.utilization_limited,
            )
        ):
            parts.append(f"лимит: {snap.limit_summary()}")
        self._now.setText(
            f"{tr('Сейчас', 'Current')}: "
            + (" · ".join(parts) if parts else tr("нет данных", "no data"))
        )

        # На график — только полные снимки: частичная точка соврала бы картинку.
        if temp is not None and power is not None and freq is not None:
            t = time.monotonic() - self._t0
            if self._t_last is not None and t - self._t_last > _GAP_MAX_S:
                self._live.clear()  # пауза (сон/зависание) — не мостим линию
            self._live.push(t, temp, power, freq)
            self._t_last = t

    def display_result(self, result: TuningResult) -> None:
        """Итог поиска: состояние карты, лучшее стабильное значение, шаги.

        Главное — честно отвечает на «а применилось ли?»: карта либо на
        конкретном значении (и оно названо), либо предупреждение об откате.
        """
        mode = _MODE_LABELS.get(result.mode, result.mode)
        mode_en = _MODE_LABELS_EN.get(result.mode, result.mode)
        self._state.setText(
            tr(f"Итог: {result.stopped_reason}", f"Result: {result.stopped_reason}")
        )
        lines = [
            tr(
                f"Режим {mode}: база {result.baseline:+g} → лучший стабильный "
                f"{result.best_stable:+g} (шагов: {result.steps_taken})",
                f"Mode {mode_en}: baseline {result.baseline:+g} → best stable "
                f"{result.best_stable:+g} (steps: {result.steps_taken})",
            )
        ]
        if result.card_value is None:
            lines.append(
                tr(
                    "⚠ КАРТА НЕ на стабильном значении — откат не удался, "
                    "сбросьте настройки на вкладке «Разгон»",
                    "⚠ CARD IS NOT at a stable value — rollback failed; reset settings "
                    "on the Overclock tab.",
                )
            )
        else:
            lines.append(
                tr(
                    f"✓ Карта оставлена на {result.card_value:+g} (сбросится при перезагрузке ОС)",
                    f"✓ Card left at {result.card_value:+g} (resets when the OS restarts)",
                )
            )
        if result.baseline != 0.0:
            lines.append(
                tr(
                    "база ≠ 0: предыдущий разгон остался на карте — поиск шёл от него "
                    "(сброс перед поиском — на вкладке «Разгон»)",
                    "Baseline ≠ 0: the previous overclock remained active and search started "
                    "from it (reset it on the Overclock tab before searching).",
                )
            )
        rows = [self._format_step(e) for e in result.history[-20:]]
        skipped = len(result.history) - len(rows)
        if skipped > 0:
            rows.insert(
                0,
                tr(
                    f"… {skipped} ранних шагов не показаны",
                    f"… {skipped} earlier steps not shown",
                ),
            )
        lines.append("\n".join(rows) if rows else tr("шагов не было", "no steps taken"))
        self._result.setText("\n".join(lines))

    @staticmethod
    def _format_step(entry: dict) -> str:
        """Строка истории шага; None-датчики не печатаются (не падают).

        Шаг в истории — только ЗАЧИТАННЫЙ (движок пишет после стабильности),
        поэтому «применён» здесь честно.
        """
        parts = [
            tr(
                f"шаг {entry.get('step', 0)}: {entry.get('value', 0.0):+g} применён",
                f"step {entry.get('step', 0)}: {entry.get('value', 0.0):+g} applied",
            )
        ]
        if entry.get("temp_c") is not None:
            parts.append(f"{entry['temp_c']:.0f}°C")
        if entry.get("power_percent") is not None:
            parts.append(f"{entry['power_percent']:.0f}%")
        if entry.get("fitness") is not None:
            parts.append(f"{tr('качество', 'fitness')} {entry['fitness']:.2f}")
        return "  " + ", ".join(parts)

    def set_busy(self, busy: bool) -> None:
        """Воркер занят поиском: «Стоп» активно, остальное заблокировано.

        Профили тоже: загрузка во время поиска подменила бы _config, пока
        идущий поиск работает со старым объектом — пользователь видел бы
        «применено», которого на самом деле нет.
        """
        self._start_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._mode_btn.setEnabled(not busy)
        self._save_btn.setEnabled(not busy)
        self._load_btn.setEnabled(not busy)

    # --- профили ---

    def _on_save_profile(self) -> None:
        """Сохраняет текущую конфигурацию по выбранному пути (реальному)."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Сохранить профиль авторазгона", "Save auto-tuning profile"),
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
            self._show_error(
                tr(f"Не удалось сохранить профиль: {e}", f"Failed to save profile: {e}")
            )
            return
        self._state.setText(tr(f"Профиль сохранён: {saved.name}", f"Profile saved: {saved.name}"))

    def _on_load_profile(self) -> None:
        """Загружает профиль из выбранного файла.

        Посторонние ключи JSON игнорируются (к полям TuningConfig не сводятся) —
        чужие/старые файлы не роняют вкладку.
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Загрузить профиль авторазгона", "Load auto-tuning profile"),
            str(self._profiles.dir),
            "JSON (*.json)",
        )
        if not path:
            return
        try:
            data = self._profiles.load(Path(path))
        except (OSError, ValueError) as e:
            self._show_error(
                tr(f"Не удалось загрузить профиль: {e}", f"Failed to load profile: {e}")
            )
            return
        allowed = {f.name for f in fields(TuningConfig)}
        self._config = TuningConfig(**{k: v for k, v in data.items() if k in allowed})
        self._mode_btn.setText(self._mode_text())
        self._state.setText(
            tr(f"Профиль загружен: {Path(path).name}", f"Profile loaded: {Path(path).name}")
        )

    def _show_error(self, text: str) -> None:
        QMessageBox.warning(self, tr("Авторазгон", "Auto-tuning"), text)


__all__ = ["AutoTuneTab"]
