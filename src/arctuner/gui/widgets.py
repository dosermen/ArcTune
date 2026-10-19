"""Общие виджеты GUI: карточка метрики и диалог подтверждения записи."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


class MetricCard(QFrame):
    """Карточка одного датчика: крупное значение + подпись + цветной маркер.

    Маркер ("temp"/"power"/"freq") — тот же цвет, что у соответствующей линии
    живого графика (константы из theme.py): дашборд и график связаны глазом.
    """

    def __init__(
        self,
        title: str,
        unit: str = "",
        parent: QWidget | None = None,
        accent: str | None = None,
        note: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self._unit = unit
        if accent is not None:
            self.setProperty("accent", accent)  # QSS [accent=…] подсвечивает верх

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

        if note:
            # Подсказка на карточку и на оба поля: наведение на любое место
            # показывает пояснение (у QLabel своя зона событий, у фрейма — своя).
            for widget in (self, self._value, self._title):
                widget.setToolTip(note)
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
                widget.installEventFilter(self)
        self._note = note

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (имя Qt API)
        """Клик по карточке с подсказкой — явное окно с полным пояснением.

        Наведение даёт быстрый tooltip, клик — стабильное окно (не исчезает,
        пока не закрыто). Клик поглощается: под ним нет других действий.
        """
        if self._note and event.type() == QEvent.Type.MouseButtonPress:
            self.show_note()
            return True
        return super().eventFilter(obj, event)

    def show_note(self) -> None:
        """Показывает пояснение карточки отдельным окном (явный запрос)."""
        if not self._note:
            return
        QMessageBox.information(self.window(), self._title.text(), self._note)

    def set_value(
        self,
        value: float | int | None,
        digits: int = 0,
        *,
        unit: str | None = None,
        tooltip: str = "",
    ) -> None:
        """Показывает значение; None -> «—» (датчик недоступен).

        Args:
            value: значение датчика; None — нет данных.
            digits: знаков после запятой (для float).
            unit: переопределить единицу (по умолчанию — подпись карточки).
            tooltip: подпись при наведении (вторичная информация).
        """
        self._value.setToolTip(tooltip)
        if value is None:
            self._value.setText("—")
            return
        shown = self._unit if unit is None else unit
        if isinstance(value, float):
            if value == 0.0:
                value = 0.0  # −0.0 печаталось как «-0.000» и смущало
            text = f"{value:.{digits}f}"
        else:
            text = str(value)
        self._value.setText(f"{text} {shown}".rstrip())


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
