"""Вкладка «V/F»: кривая напряжение/частота, экспорт и ⚠️ запись из CSV."""

from __future__ import annotations

import csv
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

from arctuner.gui.charts import VfChart
from arctuner.gui.widgets import confirm_write
from arctuner.tuner import (
    SafetyLimitError,
    VfPoint,
    validate_vf_curve,
)


class VfTab(QWidget):
    """Просмотр кривой V/F + правка точек на графике + CSV-экспорт/импорт (с валидацией)."""

    export_requested = Signal(str)  # путь к CSV
    apply_requested = Signal(object)  # list[VfPoint]
    refresh_requested = Signal()  # перечитать кривую с карты

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[int, int]] = []  # сток с карты
        self._edited: list[tuple[int, int]] | None = None  # правка на графике
        self._busy = False  # воркер занят: устаревшие данные не снимают блок

        self._chart = VfChart()
        self._chart.curve_edited.connect(self._on_chart_edit)

        self._info = QLabel("Точек: —")
        self._info.setObjectName("hintLabel")

        self._export_btn = QPushButton("Экспорт в CSV…")
        self._export_btn.setEnabled(False)  # до передачи кривой из IGCL
        self._export_btn.clicked.connect(self._on_export)
        self._import_btn = QPushButton("Записать из CSV…")
        self._import_btn.setObjectName("dangerButton")
        self._import_btn.setEnabled(False)  # запись без кривой невозможна
        self._import_btn.clicked.connect(self._on_import)
        self._apply_chart_btn = QPushButton("Записать из графика…")
        self._apply_chart_btn.setObjectName("dangerButton")
        self._apply_chart_btn.setEnabled(False)  # включится после правки точки
        self._apply_chart_btn.clicked.connect(self._on_apply_chart)
        self._undo_btn = QPushButton("Отменить правку")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._on_undo)
        self._refresh_btn = QPushButton("Обновить")
        self._refresh_btn.clicked.connect(self.refresh_requested)

        btns = QHBoxLayout()
        btns.addWidget(self._refresh_btn)
        btns.addWidget(self._export_btn)
        btns.addStretch(1)  # чтение слева, записи справа — «опасное» визуально обособлено
        btns.addWidget(self._import_btn)
        btns.addWidget(self._apply_chart_btn)
        btns.addWidget(self._undo_btn)

        note = QLabel(
            "Кривую можно править мышью прямо на графике (потяните точку: по вертикали — "
            "напряжение 600…1250 мВ, по горизонтали — между соседями точек). Запись — "
            "по кнопке с подтверждением; либо через CSV (voltage_mv,freq_mhz). Перед отправкой "
            "в драйвер кривая проходит валидацию: границы и монотонность."
        )
        note.setObjectName("hintLabel")
        note.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addLayout(btns)
        lay.addWidget(self._chart, stretch=1)
        lay.addWidget(self._info)
        lay.addWidget(note)

    def set_curve(self, points: list[tuple[int, int]]) -> None:
        """Принимает пары (мВ, МГц) от worker'а и рисует кривую."""
        self._points = list(points)
        self._edited = None
        self._chart.set_curve(self._points)
        self._chart.set_editable(bool(points))
        self._info.setText(f"Точек: {len(self._points)}" if points else "Точек: —")
        # Устаревший queued-ответ (пришёл во время busy) не снимает блок.
        self.set_busy(self._busy)

    def edited_points(self) -> list[tuple[int, int]]:
        """Текущие точки графика (с правкой или без) — для проверки курьером worker'а."""
        if self._edited is not None:
            return list(self._edited)
        return list(self._points)

    def set_busy(self, busy: bool) -> None:
        """Воркер занят записью: кнопки блокируются, после — по реальному состоянию."""
        self._busy = busy
        has_points = bool(self._points)
        self._chart.set_editable(not busy and has_points)  # правка точки — тоже запись
        self._refresh_btn.setEnabled(not busy)
        self._export_btn.setEnabled(has_points and not busy)
        self._import_btn.setEnabled(has_points and not busy)
        self._undo_btn.setEnabled(self._edited is not None and not busy)
        self._apply_chart_btn.setEnabled(
            self._edited is not None and not busy and self._edited_is_valid()
        )

    def _edited_is_valid(self) -> bool:
        """Правка не нарушает границы/монотонность (клампы должны это гарантировать)."""
        if self._edited is None:
            return False
        try:
            validate_vf_curve([VfPoint(mv, freq) for mv, freq in self._edited])
        except SafetyLimitError:
            return False
        return bool(self._points) and len(self._edited) == len(self._points)

    def _on_chart_edit(self, pairs: list) -> None:
        """Живая реакция на правку точки: статус + состояние кнопок."""
        self._edited = list(pairs)
        if self._edited == self._points:
            self._edited = None
            self._info.setText(f"Точек: {len(self._points)}")
        elif self._edited_is_valid():
            self._info.setText(
                f"Точек: {len(self._edited)} — кривая изменена; запись через «Записать из графика…»"
            )
        else:
            self._info.setText("Правка отклонена валидацией (границы/монотонность)")
        # Пересчёт кнопок с учётом текущего busy (во время поиска правка не пишется).
        self.set_busy(self._busy)

    def _on_apply_chart(self) -> None:
        """Запись правки с графика: валидация → предпросмотр диффа → подтверждение."""
        pairs = self.edited_points()
        if not pairs:
            return
        points = [VfPoint(mv, freq) for mv, freq in pairs]
        try:
            validate_vf_curve(points)
        except SafetyLimitError as e:
            self._show_error(f"Кривая отклонена валидацией: {e}")
            return

        rows = [
            (p, self._points[i] if i < len(self._points) else None)
            for i, p in enumerate(points)
            if i >= len(self._points)
            or self._points[i][0] != p.voltage_mv
            or self._points[i][1] != p.freq_mhz
        ]
        if not rows:
            self._show_error("Кривая не изменилась — записывать нечего.")
            return
        preview = "\n".join(
            f"  {p.voltage_mv:g} мВ @ {p.freq_mhz:g} МГц  (сток: {old[0]:g} мВ @ {old[1]:g} МГц)"
            if old
            else f"  {p.voltage_mv:g} мВ"
            for p, old in rows[:8]
        )
        if len(rows) > 8:
            preview += f"\n  … и ещё {len(rows) - 8}"
        if confirm_write(
            self.window(),
            "Запись кривой V/F",
            [f"изменено точек: {len(rows)} из {len(points)}"],
            detail=f"Предпросмотр:\n{preview}",
        ):
            self.apply_requested.emit(points)

    def _on_undo(self) -> None:
        """Откат правки: вернуть сток с карты."""
        self._edited = None
        self._chart.set_curve(self._points)
        self._info.setText(f"Точек: {len(self._points)}" if self._points else "Точек: —")
        self.set_busy(self._busy)

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт кривой V/F", "vf_curve.csv", "CSV (*.csv)"
        )
        if path:
            self.export_requested.emit(path)

    def _on_import(self) -> None:
        """Читает CSV, валидирует БЕЗ записи, показывает предпросмотр и спрашивает."""
        path, _ = QFileDialog.getOpenFileName(self, "Запись кривой V/F из CSV", "", "CSV (*.csv)")
        if not path:
            return
        try:
            points = self._load_csv(Path(path))
        except (OSError, ValueError, KeyError) as e:
            self._show_error(f"Не удалось прочитать CSV: {e}")
            return

        try:
            validate_vf_curve(points)
        except SafetyLimitError as e:
            self._show_error(f"Кривая отклонена валидацией: {e}")
            return

        if len(self._points) and len(points) != len(self._points):
            self._show_error(
                f"Драйвер ожидает {len(self._points)} точек, в файле {len(points)} — "
                f"число точек должно совпадать со стоковой кривой."
            )
            return

        preview = "\n".join(f"  {p.voltage_mv:g} мВ → {p.freq_mhz:g} МГц" for p in points[:8])
        if len(points) > 8:
            preview += f"\n  … и ещё {len(points) - 8}"
        if confirm_write(
            self.window(),
            "Запись кривой V/F",
            [f"{path}: {len(points)} точек"],
            detail=f"Предпросмотр:\n{preview}",
        ):
            self.apply_requested.emit(points)

    @staticmethod
    def _load_csv(path: Path) -> list[VfPoint]:
        """Читает CSV в том же формате, что пишет CLI/GUI: voltage_mv,freq_mhz."""
        out: list[VfPoint] = []
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                out.append(
                    VfPoint(
                        voltage_mv=float(row["voltage_mv"]),
                        freq_mhz=float(row["freq_mhz"]),
                    )
                )
        return out

    def _show_error(self, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, "V/F", text)


__all__ = ["VfTab"]
