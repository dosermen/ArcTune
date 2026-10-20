"""Вкладка «Разгон»: запись настроек с подтверждением (⚠️ меняет карту).

Spinbox'ы жёстко ограничены min/max/step из ctlOverclockGetProperties, а перед
запросом на запись показывается диалог «было -> станет». Сама запись идёт через
GpuContext -> tuner.py: там ещё раз проверяются жёсткие инварианты проекта
(питание <= 120%, память <= 20 Гбит/с без явного согласия) и диапазон IGCL.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, QObject, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
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
#: vramMemSpeedLimit: 22 = максимум, который декларирует драйвер (19..22).
#: >20 (номинал чипа SC20) — только после явного подтверждения «сверх номинала».
#: Живой тест: 21 Гбит/с — работает; 22 драйвер отклонил (DATA_WRITE);
#: 24 отклонено outright (вне диапазона) — выше 22 не подняться через IGCL.
_HARD_MAX: dict[str, float] = {
    "powerLimit": 120.0,  # аппаратный лимит B580 (шунт ~226 Вт)
    "vramMemSpeedLimit": 22.0,  # максимум драйвера; выше (24) — только vBIOS, вне scope
}

#: Номинал чипа памяти; значения выше — «сверх номинала», требуют сильного подтверждения.
_VRAM_NOMINAL = 20.0


class _FieldRow(QObject):
    """Контроллер одной строки разгона: имя, диапазон, спинбокс, «сейчас», кнопка.

    Не QWidget: виджеты строки раскладывает общая QGridLayout вкладки — так
    колонки выровнены по всем пунктам (раньше каждый QHBoxLayout тянулся
    независимо, и середина «плыла»). Имена виджетов (_spin/_range/_current)
    сохранены — на них завязаны тесты.
    """

    apply_requested = Signal(str, float)  # (поле, значение)

    def __init__(self, field: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._field = field
        label, unit = _FIELD_LABELS[field]
        self._supported = False  # станет True, когда IGCL подтвердит поддержку

        self._name = QLabel(label)
        self._range = QLabel("—")
        self._range.setObjectName("rangeLabel")
        self._range.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(_DECIMALS[field])
        self._spin.setEnabled(False)  # до получения диапазонов IGCL
        # Локаль «C»: десятичный разделитель — точка, как в подписях и CLI
        # (в системной локали «19,750» конфликтует с текстом вида «19.75»).
        self._spin.setLocale(QLocale.c())
        # Иначе значения вида «19,750» обрезаются: стандартный размер берётся
        # до задания диапазона/decimals. Ширину считаем по самому широкому тексту.
        self._spin.setMinimumWidth(self._required_width())

        self._unit = QLabel(unit)

        self._btn = QPushButton("Применить…")
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self._on_apply)

        self._current = QLabel("")
        self._current.setObjectName("currentLabel")
        self._current.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @property
    def widgets(self) -> tuple[QWidget, ...]:
        """Виджеты строки в порядке колонок общей сетки вкладки."""
        return (self._name, self._range, self._spin, self._unit, self._current, self._btn)

    def _required_width(self, info: OcControlInfo | None = None) -> int:
        """Минимальная ширина спинбокса по самому широкому возможному тексту.

        QDoubleSpinBox по умолчанию меряется до setRange/setDecimals, поэтому
        «19,750» в узком поле превращается в «19,7» — считаем ширину явно.
        """
        decimals = _DECIMALS[self._field]
        if info is not None:
            hi = min(info.max, _HARD_MAX.get(self._field, info.max))
            widest = f"{hi:.{decimals}f}"
        else:
            widest = "0" * 8 + "." + "0" * decimals  # консервативная оценка до IGCL
        text_width = QFontMetrics(self._spin.font()).horizontalAdvance(widest)
        # + две кнопки «стрелки вверх/вниз» и внутренние отступы поля ввода
        return text_width + 60

    def set_range(self, info: OcControlInfo, current: float | None) -> None:
        """Задаёт диапазон из IGCL (с учётом жёстких потолков) и текущее значение."""
        hard = _HARD_MAX.get(self._field)
        hi = info.max if hard is None else min(info.max, hard)
        hi = max(hi, info.min)  # защита от инверсии диапазона на странных данных
        self._spin.setRange(info.min, hi)
        if info.step > 0:
            self._spin.setSingleStep(info.step)
        if current is not None:
            self._spin.setValue(current)
            self._current.setText(f"сейчас: {current:g}")
        else:
            # Иначе остаётся текст прошлой записи — устаревшее значение.
            self._current.setText("сейчас: —")
        if hi < info.max:
            self._range.setText(f"{info.min:g}…{hi:g} (потолок проекта)")
        else:
            self._range.setText(f"{info.min:g}…{info.max:g}")
        self._spin.setMinimumWidth(self._required_width(info))
        self._supported = info.supported
        self._spin.setEnabled(self._supported)
        self._btn.setEnabled(self._supported)

    def set_busy(self, busy: bool) -> None:
        """Блокирует запись на время операции воркера (анти-двойной клик)."""
        self._spin.setEnabled(self._supported and not busy)
        self._btn.setEnabled(self._supported and not busy)

    def _on_apply(self) -> None:
        value = self._spin.value()
        label, unit = _FIELD_LABELS[self._field]
        current = self._current.text().removeprefix("сейчас: ").strip() or "?"
        hard = ""
        if self._field == "vramMemSpeedLimit" and value > _VRAM_NOMINAL:
            hard = (
                f"⚠️ СВЕРХ НОМИНАЛА чипа SC20 ({_VRAM_NOMINAL:g} {unit})! "
                f"Драйвер декларирует до {_HARD_MAX[self._field]:g}, но живой тест: "
                f"21 — работает, 22 — отказ драйвера (DATA_WRITE). Стабильность не "
                f"гарантирована: возможны артефакты/сброс драйвера."
            )
        elif self._field == "vramMemSpeedLimit":
            # _HARD_MAX для памяти = 22 (максимум драйвера), но безопасный
            # потолок проекта — 20 (номинал SC20). Раньше здесь писалось
            # «Потолок проекта: 22» и путало с безусловно разрешённым.
            hard = (
                f"Безопасный потолок проекта: {_VRAM_NOMINAL:g} {unit} (номинал чипа SC20); "
                f"драйвер допускает до {_HARD_MAX[self._field]:g}, но выше номинала — "
                f"только с явным предупреждением."
            )
        elif self._field in _HARD_MAX:
            hard = f"Потолок проекта: {_HARD_MAX[self._field]:g} {unit} (обход невозможен)."
        if self._field == "gpuVoltageOffset":
            extra = (
                "Intel требует waiver (принятие снижения срока службы) — вызывается "
                "автоматически. В МИНУС смещение недоступно (драйвер даёт 0…100): "
                "undervolt — через вкладку «V/F»."
            )
            hard = f"{hard}\n{extra}" if hard else extra
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
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        for row_index, field in enumerate(self.FIELDS):
            row = _FieldRow(field, self)
            row.apply_requested.connect(self.apply_requested)
            self._rows[field] = row
            for column, widget in enumerate(row.widgets):
                grid.addWidget(widget, row_index, column)
        # Имя и диапазон тянутся, спинбокс/единицы/кнопки — колонками по факту.
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        vbox = QVBoxLayout(box)
        vbox.addLayout(grid)
        for note_text in (
            "* Лимит температуры: драйвер отдаёт units=PERCENT, значения — °C",
            "* Смещение напряжения: только ПЛЮС (драйвер даёт 0…100); в минус —"
            " только через кривую V/F (вкладка «V/F»).",
        ):
            note = QLabel(note_text)
            note.setObjectName("hintLabel")
            note.setWordWrap(True)
            vbox.addWidget(note)

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

    def set_busy(self, busy: bool) -> None:
        """Воркер занят: блокируем «Применить…» во всех строках и сброс."""
        for row in self._rows.values():
            row.set_busy(busy)
        self._reset.setEnabled(not busy)

    def _on_reset(self) -> None:
        if confirm_write(
            self.window(),
            "Сброс настроек",
            ["Все OC-пункты → значения по умолчанию"],
            detail="Сброс идёт через ctlOverclockResetToDefault и журналируется.",
        ):
            self.reset_requested.emit()


__all__ = ["OverclockTab"]
