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

from arctuner.context import MIN_FAN_RPM
from arctuner.gui.context import FanInfo
from arctuner.gui.widgets import confirm_write
from arctuner.i18n import tr


class FansTab(QWidget):
    """Показывает вентиляторы и даёт фиксацию общей скорости / возврат в авто."""

    set_fan_requested = Signal(int)  # RPM
    reset_fans_requested = Signal()
    refresh_requested = Signal()  # перечитать RPM с карты

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # (индекс, RPM-лейбл, props-лейбл, контейнер строки) — держим все виджеты,
        # иначе при обновлении списка старые holder'ы копятся в layout.
        self._rows: list[tuple[int, QLabel, QLabel, QWidget]] = []
        self._controllable = 0
        self._have_fans = False
        self._busy = False  # воркер занят: устаревший queued-список не снимает блок

        self._list_box = QGroupBox(tr("Вентиляторы (только чтение)", "Fans (read-only)"))
        self._list_layout = QVBoxLayout(self._list_box)

        self._rpm = QSpinBox()
        # Минимум — жёсткий пол проекта (MIN_FAN_RPM): ниже вентилятор может
        # не стартовать. maxRPM уточняется при каждом обновлении.
        self._rpm.setRange(int(MIN_FAN_RPM), 3000)
        self._rpm.setSingleStep(100)
        self._rpm.setSuffix(" RPM")
        self._rpm.setValue(1500)

        self._set_btn = QPushButton(tr("⚠️ Зафиксировать все…", "⚠️ Set all fans…"))
        self._set_btn.setEnabled(False)  # до подтверждения canControl из IGCL
        self._set_btn.clicked.connect(self._on_set)

        self._reset_btn = QPushButton(tr("Вернуть в авто", "Restore automatic control"))
        self._reset_btn.setEnabled(False)  # до первого списка вентиляторов
        self._reset_btn.clicked.connect(self._on_reset)

        self._refresh_btn = QPushButton(tr("Обновить", "Refresh"))
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel(tr("Общая скорость:", "Fan speed:")))
        ctrl.addWidget(self._rpm)
        ctrl.addWidget(self._set_btn)
        ctrl.addWidget(self._reset_btn)
        ctrl.addWidget(self._refresh_btn)
        ctrl.addStretch(1)

        self._note = QLabel(
            tr(
                "B580 поддерживает только FIXED-режим (нет таблицы temp/speed), единица — RPM.",
                "B580 supports FIXED mode only (no temperature/speed curve); values are in RPM.",
            )
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
        # removeWidget+hide убирают строку мгновенно, deleteLater освобождает
        # память при возврате в event loop (без этого holder'ы копились в layout).
        for _, _, _, holder in self._rows:
            self._list_layout.removeWidget(holder)
            holder.hide()
            holder.deleteLater()
        self._rows.clear()

        self._have_fans = bool(fans)
        self._controllable = sum(1 for f in fans if f.props.canControl)
        max_rpm = max((f.props.maxRPM for f in fans if f.props.maxRPM > 0), default=3000)
        self._rpm.setRange(int(MIN_FAN_RPM), int(max_rpm))

        if not fans:
            empty = QLabel(
                tr(
                    "Вентиляторы не найдены (внешний кулер / ноутбук?).",
                    "No fans found (external cooler or laptop?).",
                )
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_layout.addWidget(empty)
            # empty сам себе контейнер: очистка выше корректно уберёт его ниже.
            self._rows.append((-1, empty, QLabel(""), empty))

        for fan in fans:
            # rpm < 0 — датчик недоступен, честно показываем «—», а не «-1 RPM».
            speed_text = f"[{fan.index}]  {fan.rpm} RPM" if fan.rpm >= 0 else f"[{fan.index}]  —"
            speed = QLabel(speed_text)
            speed.setObjectName("fanSpeed")
            modes = "/".join(
                m
                for bit, m in ((1, "default"), (2, "fixed"), (4, "table"))
                if fan.props.supportedModes & bit
            )
            props = QLabel(
                f"max {fan.props.maxRPM}  {tr('режимы', 'modes')}: {modes or '-'}  "
                f"{tr('управляемый', 'controllable')}: "
                f"{tr('да', 'yes') if fan.props.canControl else tr('нет', 'no')}"
            )
            props.setObjectName("hintLabel")
            row = QHBoxLayout()
            row.addWidget(speed)
            row.addWidget(props)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            self._list_layout.addWidget(holder)
            self._rows.append((fan.index, speed, props, holder))

        # Устаревший queued-список (пришёл во время busy) не должен снимать
        # блокировку записей — пересчитываем кнопки по ТЕКУЩЕМУ busy.
        self.set_busy(self._busy)

    def set_busy(self, busy: bool) -> None:
        """Воркер занят записью: кнопки блокируются, после — по реальному состоянию."""
        self._busy = busy
        self._set_btn.setEnabled(self._controllable > 0 and not busy)
        self._reset_btn.setEnabled(self._have_fans and not busy)
        self._refresh_btn.setEnabled(not busy)

    def _on_set(self) -> None:
        rpm = self._rpm.value()
        detail = tr(
            "Вернуть автоуправление: вкладка «Вентиляторы» → «Вернуть в авто».",
            "To restore automatic control, use Fans → Restore automatic control.",
        )
        if rpm <= MIN_FAN_RPM:
            detail += tr(
                f"\n⚠️ {rpm} RPM — минимально допустимая фиксация: "
                "под нагрузкой возможен перегрев — "
                "следите за температурой в «Мониторинге».",
                f"\n⚠️ {rpm} RPM is the minimum allowed setting. Overheating may occur "
                "under load; monitor temperatures on the Monitoring tab.",
            )
        if confirm_write(
            self.window(),
            tr("Фиксация вентиляторов", "Set fan speed"),
            [
                tr(
                    f"Все управляемые вентиляторы → {rpm} RPM (FIXED)",
                    f"All controllable fans → {rpm} RPM (FIXED)",
                )
            ],
            detail=detail,
        ):
            self.set_fan_requested.emit(rpm)

    def _on_reset(self) -> None:
        if confirm_write(
            self.window(),
            tr("Возврат вентиляторов", "Restore fans"),
            [tr("Все вентиляторы → аппаратный режим (авто)", "All fans → hardware automatic mode")],
        ):
            self.reset_fans_requested.emit()


__all__ = ["FansTab"]
