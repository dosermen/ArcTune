"""Вкладка «Вентиляторы»: чтение RPM + ⚠️ фиксация скорости (как CLI fans --set)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.context import FanInfo
from arctuner.gui.widgets import confirm_write


class FansTab(QWidget):
    """Показывает вентиляторы и даёт фиксацию общей скорости / возврат в авто."""

    set_fan_requested = Signal(int)  # RPM
    reset_fans_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[int, QLabel, QLabel]] = []

        self._list_box = QGroupBox("Вентиляторы (read-only)")
        self._list_layout = QVBoxLayout(self._list_box)

        self._rpm = QSpinBox()
        self._rpm.setRange(0, 3000)  # maxRPM уточняется при каждом обновлении
        self._rpm.setSingleStep(100)
        self._rpm.setSuffix(" RPM")
        self._rpm.setValue(1500)

        self._set_btn = QPushButton("⚠️ Зафиксировать все…")
        self._set_btn.clicked.connect(self._on_set)

        self._reset_btn = QPushButton("Вернуть в авто")
        self._reset_btn.clicked.connect(self._on_reset)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Общая скорость:"))
        ctrl.addWidget(self._rpm)
        ctrl.addWidget(self._set_btn)
        ctrl.addWidget(self._reset_btn)
        ctrl.addStretch(1)

        self._note = QLabel(
            "B580 поддерживает только FIXED-режим (нет таблицы temp/speed), единица — RPM."
        )
        self._note.setObjectName("hintLabel")
        self._note.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(self._list_box)
        lay.addLayout(ctrl)
        lay.addWidget(self._note)
        lay.addStretch(1)

    def set_fans(self, fans: list[FanInfo]) -> None:
        """Перерисовывает список: RPM, max, режимы, управляемость."""
        for _, speed_label, props_label in self._rows:
            speed_label.deleteLater()
            props_label.deleteLater()
        self._rows.clear()

        if not fans:
            empty = QLabel("Вентиляторы не найдены (внешний кулер / ноутбук?).")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_layout.addWidget(empty)
            self._rows.append((-1, empty, QLabel("")))
            return

        max_rpm = max((f.props.maxRPM for f in fans if f.props.maxRPM > 0), default=3000)
        self._rpm.setRange(0, int(max_rpm))

        for fan in fans:
            speed = QLabel(f"[{fan.index}]  {fan.rpm:>4} RPM")
            speed.setObjectName("fanSpeed")
            modes = "/".join(
                m
                for bit, m in ((1, "default"), (2, "fixed"), (4, "table"))
                if fan.props.supportedModes & bit
            )
            props = QLabel(
                f"max {fan.props.maxRPM}  режимы: {modes or '-'}  "
                f"управляемый: {'да' if fan.props.canControl else 'нет'}"
            )
            props.setObjectName("hintLabel")
            row = QHBoxLayout()
            row.addWidget(speed)
            row.addWidget(props)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            self._list_layout.addWidget(holder)
            self._rows.append((fan.index, speed, props))

        controllable = sum(1 for f in fans if f.props.canControl)
        self._set_btn.setEnabled(controllable > 0)

    def _on_set(self) -> None:
        rpm = self._rpm.value()
        if confirm_write(
            self.window(),
            "Фиксация вентиляторов",
            [f"Все управляемые вентиляторы → {rpm} RPM (FIXED)"],
            detail="Вернуть автоуправление: вкладка «Вентиляторы» → «Вернуть в авто».",
        ):
            self.set_fan_requested.emit(rpm)

    def _on_reset(self) -> None:
        if confirm_write(
            self.window(),
            "Возврат вентиляторов",
            ["Все вентиляторы → аппаратный режим (авто)"],
        ):
            self.reset_fans_requested.emit()


__all__ = ["FansTab"]
