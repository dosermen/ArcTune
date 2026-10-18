"""Вкладка «Разгон»: запись настроек с подтверждением (⚠️ меняет карту).

Spinbox'ы жёстко ограничены min/max/step из ctlOverclockGetProperties, а перед
запросом на запись показывается диалог «было -> станет». Сама запись идёт через
GpuContext -> tuner.py: там ещё раз проверяются жёсткие инварианты проекта
(питание <= 120%, память <= 20 Гбит/с без явного согласия) и диапазон IGCL.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.widgets import confirm_write
from arctuner.igcl.api import OcControlInfo

#: Пункт UI: ключ в oc-словаре, русское имя, подпись единиц.
#: temperatureLimit: драйвер B580 отдаёт units=PERCENT, но значения — °C
#: (60..100). Подписываем честно по фактическому смыслу, с пометкой.
_FIELD_LABELS: dict[str, tuple[str, str]] = {
    "powerLimit": ("Лимит питания", "%"),
    "gpuFrequencyOffset": ("Смещение частоты ядра", "МГц"),
    "gpuVoltageOffset": ("Смещение напряжения", "%"),
    "temperatureLimit": ("Лимит температуры", "°C*"),
    "vramMemSpeedLimit": ("Скорость памяти", "Гбит/с"),
}

#: Сколько знаков после запятой показывать (шаг памяти 0.001 — иначе 0.00).
_DECIMALS: dict[str, int] = {
    "powerLimit": 0,
    "gpuFrequencyOffset": 0,
    "gpuVoltageOffset": 0,
    "temperatureLimit": 0,
    "vramMemSpeedLimit": 3,
}

#: Жёсткие потолки проекта поверх IGCL (см. tuner.py); GUI не даёт выйти выше.
_HARD_MAX: dict[str, float] = {
    "powerLimit": 120.0,  # аппаратный лимит B580 (шунт ~226 Вт)
    "vramMemSpeedLimit": 20.0,  # номинал чипа SC20; выше — только CLI --force
}


class _FieldRow(QWidget):
    """Одна строка: имя, спинбокс в диапазоне IGCL, кнопка «Применить»."""

    apply_requested = Signal(str, float)  # (поле, значение)

    def __init__(self, field: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._field = field
        label, unit = _FIELD_LABELS[field]

        self._name = QLabel(label)
        self._range = QLabel("—")
        self._range.setObjectName("rangeLabel")

        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(_DECIMALS[field])
        self._spin.setEnabled(False)  # до получения диапазонов IGCL

        self._unit = QLabel(unit)

        self._btn = QPushButton("Применить…")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self._on_apply)

        self._current = QLabel("")
        self._current.setObjectName("currentLabel")

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.addWidget(self._name, stretch=2)
        row.addWidget(self._range, stretch=2)
        row.addWidget(self._spin)
        row.addWidget(self._unit)
        row.addWidget(self._current)
        row.addWidget(self._btn)

    def set_range(self, info: OcControlInfo, current: float | None) -> None:
        """Задаёт диапазон из IGCL (с учётом жёстких потолков) и текущее значение."""
        hi = min(info.max, _HARD_MAX.get(self._field, info.max))
        self._spin.setRange(info.min, hi)
        if info.step > 0:
            self._spin.setSingleStep(info.step)
        if current is not None:
            self._spin.setValue(current)
            self._current.setText(f"сейчас: {current:g}")
        self._range.setText(f"{info.min:g}…{info.max:g}")
        self._spin.setEnabled(info.supported)
        self._btn.setEnabled(info.supported)

    def _on_apply(self) -> None:
        value = self._spin.value()
        label, unit = _FIELD_LABELS[self._field]
        current = self._current.text().removeprefix("сейчас: ").strip() or "?"
        hard = ""
        if self._field in _HARD_MAX:
            hard = f"Потолок проекта: {_HARD_MAX[self._field]:g} {unit} (обход невозможен)."
        if confirm_write(
            self.window(),
            "Подтверждение записи",
            [f"{label}: {current} → {value:g} {unit}"],
            detail=hard,
        ):
            self.apply_requested.emit(self._field, value)


class OverclockTab(QWidget):
    """Все регулируемые пункты разгона + сброс к дефолту."""

    apply_requested = Signal(str, float)
    reset_requested = Signal()

    #: порядок пунктов в UI
    FIELDS = (
        "powerLimit",
        "gpuFrequencyOffset",
        "gpuVoltageOffset",
        "temperatureLimit",
        "vramMemSpeedLimit",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, _FieldRow] = {}

        box = QGroupBox("Пункты разгона (диапазоны — из IGCL)")
        vbox = QVBoxLayout(box)
        for field in self.FIELDS:
            row = _FieldRow(field)
            row.apply_requested.connect(self.apply_requested)
            self._rows[field] = row
            vbox.addWidget(row)
        vbox.addWidget(QLabel("* Лимит температуры: драйвер отдаёт units=PERCENT, значения — °C"))

        self._reset = QPushButton("⚠️ Сбросить всё к дефолту…")
        self._reset.clicked.connect(self._on_reset)

        hint = QLabel("Запись применяется не мгновенно — эффект виден в «Мониторинге».")
        hint.setObjectName("hintLabel")

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(self._reset)
        lay.addWidget(hint)
        lay.addStretch(1)

    def set_ranges(self, ranges: dict, currents: dict) -> None:
        """Обновляет диапазоны/текущие значения из oc_ready."""
        for field, row in self._rows.items():
            info = ranges.get(field)
            if isinstance(info, OcControlInfo):
                row.set_range(info, currents.get(field))

    def _on_reset(self) -> None:
        if confirm_write(
            self.window(),
            "Сброс настроек",
            ["Все OC-пункты → значения по умолчанию"],
            detail="Сброс идёт через ctlOverclockResetToDefault и журналируется.",
        ):
            self.reset_requested.emit()


__all__ = ["OverclockTab"]
