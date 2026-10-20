"""Вкладка «Журнал»: история изменений + ⚠️ откат к исходным значениям."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.context import ROLLBACKABLE_FIELDS
from arctuner.gui.widgets import confirm_write
from arctuner.logbook import LogEntry, compute_rollback_targets


class LogTab(QWidget):
    """Показывает журнал logbook.jsonl и запускает откат (как CLI log --rollback)."""

    rollback_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[LogEntry] = []
        self._busy = False  # воркер занят: устаревшие записи не снимают блок

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Время (UTC)", "Поле", "Было", "Стало", "Примечание", "Восстановлено"]
        )
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._count = QLabel("Записей: 0")
        self._count.setObjectName("hintLabel")

        self._refresh_btn = QPushButton("Обновить")
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        self._rollback_btn = QPushButton("⚠️ Откатить к исходным…")
        self._rollback_btn.setObjectName("dangerButton")
        self._rollback_btn.setEnabled(False)  # до первого чтения журнала
        self._rollback_btn.clicked.connect(self._on_rollback)

        top = QHBoxLayout()
        top.addWidget(self._count)
        top.addStretch(1)
        top.addWidget(self._refresh_btn)
        top.addWidget(self._rollback_btn)

        note = QLabel(
            "Откат восстанавливает значения, зафиксированные в журнале до изменений. "
            "Каждая запись журналируется, поэтому история сохраняется."
        )
        note.setObjectName("hintLabel")
        note.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(note)

    def set_entries(self, entries: list[LogEntry]) -> None:
        """Заполняет таблицу (новые сверху)."""
        self._entries = list(entries)
        self._count.setText(f"Записей: {len(self._entries)}")
        # Устаревший queued-журнал (пришёл во время busy) не снимает блок отката.
        # Кнопка активна только если есть реальные цели: записи одних
        # вентиляторов/маркеры сброса откатывать нечего.
        self._rollback_btn.setEnabled(
            bool(self._rollback_targets(self._entries)) and not self._busy
        )

        rows = list(reversed(self._entries))
        self._table.setRowCount(len(rows))
        for row, e in enumerate(rows):
            # Полный ISO («2026-01-01T00:00:00+00:00») громоздок в ячейке:
            # показываем «2026-01-01 00:00», полная метка — в подсказке.
            values = [
                e.timestamp[:16].replace("T", " "),
                e.field,
                "" if e.old is None else f"{e.old:g}",
                "" if e.new is None else f"{e.new:g}",
                e.note,
                "да" if e.restored else "",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(e.timestamp)
                if col in (2, 3):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row, col, item)
        self._table.resizeColumnsToContents()

    @staticmethod
    def _rollback_targets(entries: list[LogEntry]) -> list[str]:
        """Строки предпросмотра отката с семантикой logbook.rollback_targets.

        Расчёт общий с воркером/CLI: после последнего ``reset_oc`` записи до
        маркера целями не являются. Откатываются только OC-поля
        (ROLLBACKABLE_FIELDS) — вентиляторы возвращаются кнопкой на вкладке
        «Вентиляторы», fan[*] в журнале остаётся историей.
        """
        return [
            f"{field}: восстановить {value:g}"
            for field, value in compute_rollback_targets(entries).items()
            if field in ROLLBACKABLE_FIELDS and value is not None
        ]

    def _on_rollback(self) -> None:
        targets = self._rollback_targets(self._entries)
        if not targets:
            # Защита от устаревшего включённого состояния: не показываем
            # подтверждение и не отправляем воркеру заведомо пустой откат.
            QMessageBox.information(
                self,
                "Журнал",
                "Нечего откатывать: в журнале нет записей, пригодных для отката.",
            )
            return
        preview = "\n".join(targets[:8])
        if len(targets) > 8:
            preview += f"\n  … и ещё {len(targets) - 8}"
        if confirm_write(
            self.window(),
            "Откат к исходным значениям",
            ["Вернуть настройки, зафиксированные в журнале"],
            detail=f"Будут восстановлены:\n{preview}",
        ):
            self.rollback_requested.emit()

    def set_busy(self, busy: bool) -> None:
        """Воркер занят записью: откат/обновление блокированы до его конца."""
        self._busy = busy
        self._rollback_btn.setEnabled(bool(self._rollback_targets(self._entries)) and not busy)
        self._refresh_btn.setEnabled(not busy)


__all__ = ["LogTab"]
