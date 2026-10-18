"""Общие виджеты GUI: карточка метрики и диалог подтверждения записи."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


class MetricCard(QFrame):
    """Карточка одного датчика: крупное значение + подпись."""

    def __init__(self, title: str, unit: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self._unit = unit

        self._value = QLabel("—")
        self._value.setObjectName("metricValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel(title)
        self._title.setObjectName("metricTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        lay.addWidget(self._value)
        lay.addWidget(self._title)

    def set_value(self, value: float | int | None, digits: int = 0) -> None:
        """Показывает значение; None -> «—» (датчик недоступен)."""
        if value is None:
            self._value.setText("—")
            return
        if isinstance(value, float):
            if value == 0.0:
                value = 0.0  # −0.0 печаталось как «-0.000» и смущало
            text = f"{value:.{digits}f}"
        else:
            text = str(value)
        self._value.setText(f"{text} {self._unit}".rstrip())


def confirm_write(
    parent: QWidget | None,
    title: str,
    changes: list[str],
    detail: str = "",
) -> bool:
    """Спрашивает подтверждение перед ⚠️ записью (меняет состояние карты).

    Args:
        parent: родительское окно.
        title: заголовок диалога.
        changes: строки вида «поле: было -> станет».
        detail: дополнительный текст (например, про инварианты).

    Returns:
        True — пользователь подтвердил запись.
    """
    text = "⚠️  Это ИЗМЕНИТ настройки карты (write).\n\n" + "\n".join(changes)
    if detail:
        text += f"\n\n{detail}"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton("Записать", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(box.buttons()[-1])  # по умолчанию — «Отмена»
    box.exec()
    return box.clickedButton() is yes


__all__ = ["MetricCard", "confirm_write"]
