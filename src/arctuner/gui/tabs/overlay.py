"""Вкладка «Оверлей»: настройки HUD поверх окон (без IGCL — чистый UI)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from arctuner.gui.overlay import (
    CELL_CHOICES,
    CELL_KEYS,
    LAYOUT_LABELS,
    LAYOUTS,
    OPACITIES,
    OverlayWindow,
    load_overlay_enabled,
)


class OverlayTab(QWidget):
    """Настройки оверлея: показ, макет, непрозрачность, набор метрик, позиция.

    Вкладка работает напрямую с ``OverlayWindow`` — тот хранит выбор в
    настройках и эмитит сигналы, поэтому меню «Вид → Оверлей» и правый клик
    по HUD всегда согласованы с вкладкой. Телеметрия здесь не нужна: HUD
    обновляется сам через ``telemetry_ready`` главного окна (read-only).
    """

    def __init__(self, overlay: OverlayWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay = overlay
        # Флаг-защита от петель: сигналы оверлея ставят галочки, а галочки
        # эмитят toggled — во время синхронизации обработчики молчат.
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- показ ---
        self._enable = QCheckBox("Показывать оверлей поверх окон", self)
        self._enable.setToolTip(
            "Компактный HUD всегда поверх окон: виден поверх игр и бенчмарков "
            "в оконном/безрамочном режиме."
        )
        self._enable.setChecked(load_overlay_enabled())
        self._enable.toggled.connect(self._on_show_toggled)
        root.addWidget(self._enable)

        # --- макет ---
        layout_box = QGroupBox("Макет", self)
        layout_lay = QVBoxLayout(layout_box)
        self._layout_group = QButtonGroup(self)
        self._layout_group.setExclusive(True)
        self._layout_buttons: dict[str, QRadioButton] = {}
        for name in LAYOUTS:
            button = QRadioButton(LAYOUT_LABELS[name], layout_box)
            button.toggled.connect(lambda checked, n=name: self._on_layout_toggled(checked, n))
            self._layout_group.addButton(button)
            layout_lay.addWidget(button)
            self._layout_buttons[name] = button
        layout_lay.addWidget(
            self._hint("Пресет возвращает свой набор метрик; состав правится ниже.")
        )
        root.addWidget(layout_box)

        # --- непрозрачность ---
        opacity_box = QGroupBox("Непрозрачность", self)
        opacity_lay = QHBoxLayout(opacity_box)
        self._opacity_label = QLabel(opacity_box)
        self._opacity_label.setMinimumWidth(52)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, opacity_box)
        self._opacity_slider.setRange(0, len(OPACITIES) - 1)  # индексы допустимых шагов
        self._opacity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._opacity_slider.setTickInterval(1)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_lay.addWidget(self._opacity_slider)
        opacity_lay.addWidget(self._opacity_label)
        root.addWidget(opacity_box)

        # --- состав метрик ---
        cells_box = QGroupBox("Состав метрик", self)
        cells_grid = QGridLayout(cells_box)
        self._cell_boxes: dict[str, QCheckBox] = {}
        for index, key in enumerate(CELL_KEYS):
            box = QCheckBox(CELL_CHOICES[key], cells_box)
            box.toggled.connect(lambda checked, k=key: self._on_cell_toggled(checked, k))
            cells_grid.addWidget(box, index // 2, index % 2)  # 2 колонки → 4 ряда
            self._cell_boxes[key] = box
        cells_grid.addWidget(
            self._hint("Минимум одна метрика — пустой HUD бессмыслен."), 4, 0, 1, 2
        )
        root.addWidget(cells_box)

        # --- положение ---
        position_row = QHBoxLayout()
        self._reset_position = QPushButton("Сбросить положение", self)
        self._reset_position.setToolTip("Вернуть HUD в правый верхний угол основного экрана.")
        self._reset_position.clicked.connect(self._overlay.reset_position)
        position_row.addWidget(self._reset_position)
        position_row.addStretch(1)
        root.addLayout(position_row)

        # --- подсказки об использовании и ограничении ОС ---
        hint = self._hint(
            "HUD перетаскивается мышью; правый клик — быстрое меню (макет, "
            "непрозрачность, «Скрыть»). В эксклюзивном fullscreen Windows не "
            "показывает оверлеи сторонних процессов — это ограничение ОС."
        )
        hint.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(hint)
        root.addStretch(1)

        # Виджеты подключаются к сигналам оверлея ДО стартовой синхронизации,
        # чтобы меню «Вид» и правый клик двигали вкладку всеми последующими
        # изменениями.
        overlay.visibility_changed.connect(self._sync_show)
        overlay.layout_changed.connect(self._sync_layout)
        overlay.opacity_changed.connect(self._sync_opacity)
        overlay.cells_changed.connect(self._sync_cells)
        self._sync_all()

    @staticmethod
    def _hint(text: str) -> QLabel:
        """Маленькая поясняющая подпись (перенос строк включён)."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: palette(mid); font-size: 11px;")
        return label

    # --- обработчики виджетов → OverlayWindow ---

    def _on_show_toggled(self, checked: bool) -> None:
        if not self._syncing:
            self._overlay.set_shown(checked)

    def _on_layout_toggled(self, checked: bool, name: str) -> None:
        # Снятие галочки соседней кнопки тоже эмитит toggled — работаем только
        # когда кнопку включили, иначе смена макета срабатывала бы дважды.
        if checked and not self._syncing:
            self._overlay.set_layout(name)

    def _on_opacity_changed(self, index: int) -> None:
        if not self._syncing:
            self._overlay.set_opacity(OPACITIES[index])

    def _on_cell_toggled(self, checked: bool, key: str) -> None:
        # Состояние читаем по всем галочкам сразу: снимаем сразу несколько
        # метрик — по одной посылать в оверлей бессмысленно.
        del checked
        if self._syncing:
            return
        chosen = [name for name in CELL_KEYS if self._cell_boxes[name].isChecked()]
        if not chosen:
            # Пустой HUD запрещён: возвращаем галочку на место молча.
            box = self._cell_boxes[key]
            box.blockSignals(True)
            box.setChecked(True)
            box.blockSignals(False)
            return
        self._overlay.set_visible_cells(chosen)

    # --- сигналы OverlayWindow → виджеты ---

    @Slot(bool)
    def _sync_show(self, shown: bool) -> None:
        self._syncing = True
        try:
            self._enable.setChecked(shown)
        finally:
            self._syncing = False

    @Slot(str)
    def _sync_layout(self, name: str) -> None:
        self._syncing = True
        try:
            self._layout_buttons[name].setChecked(True)
        finally:
            self._syncing = False

    @Slot(float)
    def _sync_opacity(self, value: float) -> None:
        index = next(
            (i for i, choice in enumerate(OPACITIES) if abs(choice - value) < 1e-3),
            len(OPACITIES) - 1,
        )
        self._syncing = True
        try:
            self._opacity_slider.setValue(index)
            self._opacity_label.setText(f"{value * 100:g} %")
        finally:
            self._syncing = False

    @Slot(object)
    def _sync_cells(self, keys: tuple[str, ...]) -> None:
        self._syncing = True
        try:
            for key, box in self._cell_boxes.items():
                box.setChecked(key in keys)
        finally:
            self._syncing = False

    def _sync_all(self) -> None:
        """Заполняет виджеты текущим состоянием оверлея (построение вкладки)."""
        self._sync_show(load_overlay_enabled())
        self._sync_layout(self._overlay.layout_name())
        self._sync_opacity(self._overlay.current_opacity())
        self._sync_cells(self._overlay.visible_cells())


__all__ = ["OverlayTab"]
