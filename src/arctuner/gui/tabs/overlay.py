"""Вкладка «Оверлей»: настройки HUD поверх окон (без IGCL — чистый UI)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
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

from arctuner.gui.minichart import MINI_CHART_SERIES, MINI_CHART_SIZES, MINI_CHART_WINDOWS
from arctuner.gui.overlay import (
    CELL_KEYS,
    LAYOUTS,
    OPACITIES,
    ORIENTATIONS,
    OverlayWindow,
    cell_label,
    layout_label,
    load_mini_chart_enabled,
    load_overlay_enabled,
    load_overlay_orientation,
    orientation_label,
)
from arctuner.i18n import tr


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
        self._enable = QCheckBox(
            tr("Показывать оверлей поверх окон", "Show overlay on top of windows"), self
        )
        self._enable.setToolTip(
            tr(
                "Компактный HUD всегда поверх окон: виден поверх игр и бенчмарков "
                "в оконном/безрамочном режиме.",
                "Compact HUD stays above other windows and is visible over games and "
                "benchmarks in windowed or borderless mode.",
            )
        )
        self._enable.setChecked(load_overlay_enabled())
        self._enable.toggled.connect(self._on_show_toggled)
        root.addWidget(self._enable)

        # --- макет ---
        layout_box = QGroupBox(tr("Макет", "Layout"), self)
        layout_lay = QVBoxLayout(layout_box)
        self._layout_group = QButtonGroup(self)
        self._layout_group.setExclusive(True)
        self._layout_buttons: dict[str, QRadioButton] = {}
        for name in LAYOUTS:
            button = QRadioButton(layout_label(name), layout_box)
            button.toggled.connect(lambda checked, n=name: self._on_layout_toggled(checked, n))
            self._layout_group.addButton(button)
            layout_lay.addWidget(button)
            self._layout_buttons[name] = button
        orientation_row = QHBoxLayout()
        orientation_row.addWidget(QLabel(tr("Ориентация:", "Orientation:")))
        self._orientation = QComboBox(layout_box)
        for orientation in ORIENTATIONS:
            self._orientation.addItem(orientation_label(orientation), orientation)
        self._orientation.currentIndexChanged.connect(self._on_orientation_changed)
        orientation_row.addWidget(self._orientation)
        orientation_row.addStretch(1)
        layout_lay.addLayout(orientation_row)
        layout_lay.addWidget(
            self._hint(
                tr(
                    "Пресет возвращает свой набор метрик; состав правится ниже.",
                    "Each preset restores its metric set; customize it below.",
                )
            )
        )
        root.addWidget(layout_box)

        # --- непрозрачность ---
        opacity_box = QGroupBox(tr("Непрозрачность", "Opacity"), self)
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
        cells_box = QGroupBox(tr("Состав метрик", "Metrics"), self)
        cells_grid = QGridLayout(cells_box)
        self._cell_boxes: dict[str, QCheckBox] = {}
        for index, key in enumerate(CELL_KEYS):
            box = QCheckBox(cell_label(key), cells_box)
            box.toggled.connect(lambda checked, k=key: self._on_cell_toggled(checked, k))
            cells_grid.addWidget(box, index // 2, index % 2)  # 2 колонки, рядов — по числу метрик
            self._cell_boxes[key] = box
        cells_grid.addWidget(
            self._hint(
                tr(
                    "Минимум одна метрика — пустой HUD бессмыслен.",
                    "At least one metric must remain selected.",
                )
            ),
            -(-len(CELL_KEYS) // 2),  # подсказка после последнего ряда галочек
            0,
            1,
            2,
        )
        root.addWidget(cells_box)

        # --- мини-график ---
        chart_box = QGroupBox(tr("Мини-график", "Mini chart"), self)
        chart_layout = QVBoxLayout(chart_box)
        self._chart_enable = QCheckBox(
            tr("Показывать график в оверлее", "Show chart in overlay"), chart_box
        )
        self._chart_enable.toggled.connect(self._overlay.set_mini_chart_enabled)
        chart_layout.addWidget(self._chart_enable)

        self._chart_series_boxes: dict[str, QCheckBox] = {}
        series_grid = QGridLayout()
        for index, key in enumerate(MINI_CHART_SERIES):
            box = QCheckBox(cell_label(key), chart_box)
            box.toggled.connect(lambda checked, k=key: self._on_chart_series_toggled(checked, k))
            series_grid.addWidget(box, index // 2, index % 2)
            self._chart_series_boxes[key] = box
        chart_layout.addLayout(series_grid)

        window_row = QHBoxLayout()
        window_row.addWidget(QLabel(tr("Окно графика:", "Time window:")))
        self._chart_window = QComboBox(chart_box)
        for seconds in MINI_CHART_WINDOWS:
            self._chart_window.addItem(tr(f"{seconds} с", f"{seconds} s"), seconds)
        self._chart_window.currentIndexChanged.connect(self._on_chart_window_changed)
        window_row.addWidget(self._chart_window)
        window_row.addWidget(QLabel(tr("Размер:", "Size:")))
        self._chart_size = QComboBox(chart_box)
        self._chart_size.addItem(tr("Компактный", "Compact"), "compact")
        self._chart_size.addItem(tr("Широкий", "Wide"), "wide")
        self._chart_size.currentIndexChanged.connect(self._on_chart_size_changed)
        window_row.addWidget(self._chart_size)
        window_row.addStretch(1)
        chart_layout.addLayout(window_row)
        chart_layout.addWidget(
            self._hint(
                tr(
                    "Пауза и очистка истории доступны в контекстном меню HUD.",
                    "Pause and clear history from the HUD context menu.",
                )
            )
        )
        root.addWidget(chart_box)

        # --- положение ---
        position_row = QHBoxLayout()
        self._reset_position = QPushButton(tr("Сбросить положение", "Reset position"), self)
        self._reset_position.setToolTip(
            tr(
                "Вернуть HUD в правый верхний угол основного экрана.",
                "Move the HUD back to the top-right corner of the primary screen.",
            )
        )
        self._reset_position.clicked.connect(self._overlay.reset_position)
        position_row.addWidget(self._reset_position)
        position_row.addStretch(1)
        root.addLayout(position_row)

        # --- подсказки об использовании и ограничении ОС ---
        hint = self._hint(
            tr(
                "HUD перетаскивается мышью; правый клик — быстрое меню (макет, "
                "непрозрачность, «Скрыть»). В эксклюзивном fullscreen Windows не "
                "показывает оверлеи сторонних процессов — это ограничение ОС.",
                "Drag the HUD to move it; right-click for quick settings (layout, "
                "opacity, Hide). Windows does not show third-party overlays in exclusive "
                "fullscreen mode; this is an OS limitation.",
            )
        )
        hint.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(hint)
        root.addStretch(1)

        # Виджеты подключаются к сигналам оверлея ДО стартовой синхронизации,
        # чтобы меню «Вид» и правый клик двигали вкладку всеми последующими
        # изменениями.
        overlay.visibility_changed.connect(self._sync_show)
        overlay.layout_changed.connect(self._sync_layout)
        overlay.orientation_changed.connect(self._sync_orientation)
        overlay.opacity_changed.connect(self._sync_opacity)
        overlay.cells_changed.connect(self._sync_cells)
        overlay.chart_enabled_changed.connect(self._sync_chart_enabled)
        overlay.chart_series_changed.connect(self._sync_chart_series)
        overlay.chart_window_changed.connect(self._sync_chart_window)
        overlay.chart_size_changed.connect(self._sync_chart_size)
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

    def _on_orientation_changed(self, index: int) -> None:
        if not self._syncing and index >= 0:
            orientation = str(self._orientation.itemData(index))
            if orientation in ORIENTATIONS:
                self._overlay.set_orientation(orientation)

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

    def _on_chart_series_toggled(self, checked: bool, key: str) -> None:
        del checked
        if self._syncing:
            return
        chosen = [name for name, box in self._chart_series_boxes.items() if box.isChecked()]
        if not chosen:
            box = self._chart_series_boxes[key]
            box.blockSignals(True)
            box.setChecked(True)
            box.blockSignals(False)
            return
        self._overlay.set_mini_chart_series(chosen)

    def _on_chart_window_changed(self, index: int) -> None:
        if not self._syncing and index >= 0:
            self._overlay.set_mini_chart_window(int(self._chart_window.itemData(index)))

    def _on_chart_size_changed(self, index: int) -> None:
        if not self._syncing and index >= 0:
            size = str(self._chart_size.itemData(index))
            if size in MINI_CHART_SIZES:
                self._overlay.set_mini_chart_size(size)

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

    @Slot(str)
    def _sync_orientation(self, orientation: str) -> None:
        index = self._orientation.findData(orientation)
        self._syncing = True
        try:
            self._orientation.setCurrentIndex(index)
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

    @Slot(bool)
    def _sync_chart_enabled(self, enabled: bool) -> None:
        self._syncing = True
        try:
            self._chart_enable.setChecked(enabled)
        finally:
            self._syncing = False

    @Slot(object)
    def _sync_chart_series(self, keys: tuple[str, ...]) -> None:
        self._syncing = True
        try:
            for key, box in self._chart_series_boxes.items():
                box.setChecked(key in keys)
        finally:
            self._syncing = False

    @Slot(int)
    def _sync_chart_window(self, window_s: int) -> None:
        index = self._chart_window.findData(window_s)
        self._syncing = True
        try:
            self._chart_window.setCurrentIndex(index)
        finally:
            self._syncing = False

    @Slot(str)
    def _sync_chart_size(self, size: str) -> None:
        index = self._chart_size.findData(size)
        self._syncing = True
        try:
            self._chart_size.setCurrentIndex(index)
        finally:
            self._syncing = False

    def _sync_all(self) -> None:
        """Заполняет виджеты текущим состоянием оверлея (построение вкладки)."""
        self._sync_show(load_overlay_enabled())
        self._sync_layout(self._overlay.layout_name())
        self._sync_orientation(load_overlay_orientation())
        self._sync_opacity(self._overlay.current_opacity())
        self._sync_cells(self._overlay.visible_cells())
        self._sync_chart_enabled(load_mini_chart_enabled())
        self._sync_chart_series(self._overlay.mini_chart_series())
        self._sync_chart_window(self._overlay.mini_chart_window())
        self._sync_chart_size(self._overlay.mini_chart_size())


__all__ = ["OverlayTab"]
