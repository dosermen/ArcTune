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
from arctuner.tuner import VfPoint, validate_vf_curve


class VfTab(QWidget):
    """Просмотр кривой V/F + экспорт в CSV + запись из CSV (с валидацией)."""

    export_requested = Signal(str)  # путь к CSV
    apply_requested = Signal(object)  # list[VfPoint]
    refresh_requested = Signal()  # перечитать кривую с карты

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[int, int]] = []

        self._chart = VfChart()
        self._chart.setMinimumHeight(260)

        self._info = QLabel("Точек: —")
        self._info.setObjectName("hintLabel")

        self._export_btn = QPushButton("Экспорт в CSV…")
        self._export_btn.clicked.connect(self._on_export)
        self._import_btn = QPushButton("Записать из CSV…")
        self._import_btn.setObjectName("dangerButton")
        self._import_btn.clicked.connect(self._on_import)
        self._refresh_btn = QPushButton("Обновить")
        self._refresh_btn.clicked.connect(self.refresh_requested)

        btns = QHBoxLayout()
        btns.addWidget(self._refresh_btn)
        btns.addWidget(self._export_btn)
        btns.addWidget(self._import_btn)
        btns.addStretch(1)

        note = QLabel(
            "Запись кривой — через CSV (voltage_mv,freq_mhz). До отправки в драйвер "
            "кривая проходит валидацию: границы 600…1250 мВ / 300…3300 МГц, монотонность "
            "и совпадение числа точек со стоковой."
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
        self._chart.set_curve(self._points)
        self._info.setText(f"Точек: {len(self._points)}")
        self._export_btn.setEnabled(bool(self._points))
        self._import_btn.setEnabled(bool(self._points))

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
        except ValueError as e:
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
