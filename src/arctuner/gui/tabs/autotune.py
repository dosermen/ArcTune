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

from arctuner.autofind import MODE_FREQUENCY, MODE_UNDERVOLT
from arctuner.autotune.engine import TuningConfig, TuningResult
from arctuner.autotune.profiles import ProfileStore
from arctuner.gui.charts import LiveChart
from arctuner.igcl.api import TelemetrySnapshot

#: человеческие названия режимов (кнопка-переключатель)
_MODE_LABELS: dict[str, str] = {
    MODE_FREQUENCY: "Частота (+МГц)",
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

        self._now = QLabel("Сейчас: —")
        self._now.setObjectName("hintLabel")

        self._result = QLabel("Результат: —")
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
        self._live.setMinimumHeight(200)
        self._live.setMaximumHeight(300)
        live_caption = QLabel("Живая телеметрия поиска (°C / % слева, МГц справа, окно 2 мин):")
        live_caption.setObjectName("hintLabel")

        self._save_btn = QPushButton("💾 Сохранить профиль…")
        self._save_btn.setObjectName("infoButton")
        self._save_btn.clicked.connect(self._on_save_profile)

        self._load_btn = QPushButton("📂 Загрузить профиль…")
        self._load_btn.setObjectName("infoButton")
        self._load_btn.clicked.connect(self._on_load_profile)

        profiles = QHBoxLayout()
        profiles.addWidget(QLabel("Профили:"))
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
        return f"Режим: {_MODE_LABELS.get(self._config.mode, self._config.mode)}"

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
                "ArcTune — нагрузчик из профиля",
                f"Будет запущена команда из профиля:\n{self._config.external_cmd}\n\nПродолжить?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._state.setText("Запуск отменён (нагрузчик из профиля не подтверждён)")
                return
        # Новый поиск — новый график: старые точки к нему не относятся.
        self._t0 = time.monotonic()
        self._t_last = None
        self._live.clear()
        self._now.setText("Сейчас: ожидание первого снимка…")
        self.set_busy(True)  # локально и сразу; повторно воркер пришлёт тот же статус
        self._state.setText(
            "Запуск: подготовка поиска… (потребуется нагрузка на GPU, см. примечание выше)"
        )
        self.search_requested.emit(self._config)

    # --- сигналы от worker'а ---

    def update_progress(self, current: int, total: int, text: str) -> None:
        """Живой статус: этапы (current=0) и успешные шаги (current=номер)."""
        if current > 0 and total > 0:
            self._state.setText(f"Поиск: шаг {current}/{total} — {text}")
        else:
            self._state.setText(f"Поиск: {text}")

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
        self._now.setText("Сейчас: " + (" · ".join(parts) if parts else "нет данных"))

        # На график — только полные снимки: частичная точка соврала бы картинку.
        if None not in (temp, power, freq):
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
        self._state.setText(f"Итог: {result.stopped_reason}")
        lines = [
            f"Режим {result.mode}: база {result.baseline:+g} → лучший стабильный "
            f"{result.best_stable:+g} (шагов: {result.steps_taken})"
        ]
        if result.card_value is None:
            lines.append(
                "⚠ КАРТА НЕ на стабильном значении — откат не удался, "
                "сбросьте настройки на вкладке «Разгон»"
            )
        else:
            lines.append(
                f"✓ Карта оставлена на {result.card_value:+g} (сбросится при перезагрузке ОС)"
            )
        if result.baseline != 0.0:
            lines.append(
                "база ≠ 0: предыдущий разгон остался на карте — поиск шёл от него "
                "(сброс перед поиском — на вкладке «Разгон»)"
            )
        rows = [self._format_step(e) for e in result.history[-20:]]
        skipped = len(result.history) - len(rows)
        if skipped > 0:
            rows.insert(0, f"… {skipped} ранних шагов не показаны")
        lines.append("\n".join(rows) if rows else "шагов не было")
        self._result.setText("\n".join(lines))

    @staticmethod
    def _format_step(entry: dict) -> str:
        """Строка истории шага; None-датчики не печатаются (не падают).

        Шаг в истории — только ЗАЧИТАННЫЙ (движок пишет после стабильности),
        поэтому «применён» здесь честно.
        """
        parts = [f"шаг {entry.get('step', 0)}: {entry.get('value', 0.0):+g} применён"]
        if entry.get("temp_c") is not None:
            parts.append(f"{entry['temp_c']:.0f}°C")
        if entry.get("power_percent") is not None:
            parts.append(f"{entry['power_percent']:.0f}%")
        if entry.get("fitness") is not None:
            parts.append(f"fitness {entry['fitness']:.2f}")
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
