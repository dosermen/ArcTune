"""Тема GUI ArkTune: палитры + генерируемый QSS + цвета серий графиков.

Ноль внешних зависимостей: QSS собирается строкой из палитры (темной/светлой),
никаких .qss-файлов и ресурсов — в exe ничего дополнительно паковать не нужно.

Единый источник правды для цветов: линии графиков (charts.py) и маркеры
карточек (QSS [accent=…]) берут одни и те же константы из этого модуля —
дашборд и график читаются как одно целое.

Тема применяетея в main.py до создания окна (Fusion + QPalette + QSS) и может
переключаться на ходу из меню «Вид» (окно пересобирает QSS и концы графиков).
"""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# --- цвета серий графиков (одинаковы в обеих темах — контрастные и чистые) ---

SERIES_TEMP: Final = "#e05555"  # температура
SERIES_POWER: Final = "#e0a13a"  # мощность
SERIES_FREQ: Final = "#4a90d9"  # частота

#: kind → цвет маркера карточки (тот же цвет, что у линии графика)
ACCENT_BY_KIND: Final[dict[str, str]] = {
    "temp": SERIES_TEMP,
    "power": SERIES_POWER,
    "freq": SERIES_FREQ,
}

# --- палитры UI ---

_DARK: Final[dict[str, str]] = {
    "bg": "#232629",
    "surface": "#2d3035",
    "border": "#3e4249",
    "text": "#e8e8e8",
    "hint": "#9aa0a6",
    "danger": "#e05555",
    "ok": "#57c266",
    "warn": "#e0a13a",
    "accent": "#4a90d9",
    "on_accent": "#ffffff",
}

_LIGHT: Final[dict[str, str]] = {
    "bg": "#f4f4f5",
    "surface": "#ffffff",
    "border": "#d8d8d8",
    "text": "#1e1e1e",
    "hint": "#6f7276",
    "danger": "#c62828",
    "ok": "#2e7d32",
    "warn": "#b26a00",
    "accent": "#4a90d9",
    "on_accent": "#ffffff",
}

THEMES: Final[tuple[str, ...]] = ("dark", "light")
DEFAULT_THEME: Final = "dark"

# --- хранение выбора (тот же файл настроек, что и масштаб: ArcTune/ArcTune) ---


def load_theme() -> str:
    """Тема из настроек; мусор/отсутствие принимается за дефолт (тёмная)."""
    value = QSettings("ArcTune", "ArcTune").value("ui/theme", DEFAULT_THEME)
    return value if value in THEMES else DEFAULT_THEME


def save_theme(name: str) -> None:
    """Сохраняет тему; применяется немедленно вызывающим (main.py/окно)."""
    if name not in THEMES:
        raise ValueError(f"неизвестная тема: {name!r} (ждём {THEMES})")
    QSettings("ArcTune", "ArcTune").setValue("ui/theme", name)


# --- генерация QSS из палитры ---


def _rgba(hex_color: str, alpha: float) -> str:
    """hex (#rrggbb) → rgba(r, g, b, a) для QSS-фонов."""
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def qss(dark: bool) -> str:
    """QSS всей темы из caller'а (единственные px — масштабируются QT_SCALE_FACTOR).

    Осознанные исключения: без font-size для QDoubleSpinBox/value-полей —
    тест ширины спинбокса считает QFontMetrics от до-стилевого шрифта, и
    любое вмешательство в их метрики меняет геометрию.
    """
    p = _DARK if dark else _LIGHT
    return f"""
/* --- база --- */
QMainWindow, QDialog {{
    background: {p["bg"]}; color: {p["text"]};
}}
QToolTip {{
    background: {p["surface"]}; color: {p["text"]};
    border: 1px solid {p["border"]}; padding: 4px;
}}

/* --- вкладки --- */
QTabWidget::pane {{
    border: 1px solid {p["border"]}; border-radius: 6px; top: -1px;
}}
QTabBar::tab {{
    background: {p["bg"]}; color: {p["hint"]};
    padding: 7px 16px; margin-right: 2px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {p["text"]}; border-bottom: 2px solid {p["accent"]}; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {p["text"]}; }}

/* --- карточки датчика: рамка + цветной маркер в цвет линии графика --- */
QFrame#metricCard {{
    background: {p["surface"]};
    border: 1px solid {p["border"]};
    border-top: 3px solid {_rgba(p["accent"], 0.35)};
    border-radius: 8px;
}}
QFrame#metricCard[accent="temp"]  {{ border-top-color: {ACCENT_BY_KIND["temp"]}; }}
QFrame#metricCard[accent="power"] {{ border-top-color: {ACCENT_BY_KIND["power"]}; }}
QFrame#metricCard[accent="freq"]  {{ border-top-color: {ACCENT_BY_KIND["freq"]}; }}
QLabel#metricValue {{
    font-size: 21px; font-weight: 600; color: {p["text"]}; background: transparent;
}}
QLabel#metricTitle {{
    font-size: 11px; color: {p["hint"]}; background: transparent;
}}

/* --- метки-подсказки и плашка ограничений --- */
QLabel#hintLabel {{ color: {p["hint"]}; }}
QLabel#limitsLabel {{
    background: {_rgba(p["warn"], 0.14)}; color: {p["warn"]};
    border: 1px solid {_rgba(p["warn"], 0.45)};
    border-radius: 6px; padding: 5px; font-weight: 600;
}}
QLabel#rangeLabel, QLabel#currentLabel, QLabel#fanSpeed {{ color: {p["hint"]}; }}

/* --- кнопки: обычные сдержанные, danger — красная рамка (меняет карту!) --- */
QPushButton {{
    background: {p["surface"]}; color: {p["text"]};
    border: 1px solid {p["border"]}; border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {p["accent"]}; }}
QPushButton:disabled {{ color: {p["hint"]}; border-color: {p["border"]}; }}
QPushButton#dangerButton {{
    color: {p["danger"]};
    border: 1px solid {_rgba(p["danger"], 0.65)};
    background: {_rgba(p["danger"], 0.08)};
    font-weight: 600;
}}
QPushButton#dangerButton:hover {{ background: {_rgba(p["danger"], 0.16)}; }}
QPushButton#dangerButton:disabled {{
    color: {_rgba(p["danger"], 0.5)};
    background: {_rgba(p["danger"], 0.05)};
}}

/* --- поля ввода и спинбоксы --- */
QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {p["bg"]}; color: {p["text"]};
    border: 1px solid {p["border"]}; border-radius: 6px; padding: 3px 6px;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {p["accent"]}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {p["hint"]}; }}

/* --- группы и таблица журнала --- */
QGroupBox {{
    border: 1px solid {p["border"]}; border-radius: 8px; margin-top: 10px;
    font-weight: 600; color: {p["text"]};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {p["hint"]}; }}
QTableWidget {{
    background: {p["surface"]}; color: {p["text"]};
    border: 1px solid {p["border"]}; gridline-color: {_rgba(p["border"], 0.6)};
    alternate-background-color: {p["bg"]};
        selection-background-color: {_rgba(p["accent"], 0.4)};
}}
QHeaderView::section {{
    background: {p["bg"]}; color: {p["hint"]};
    border: none; border-bottom: 1px solid {p["border"]}; padding: 5px;
}}
QTableView {{ selection-background-color: {_rgba(p["accent"], 0.4)}; }}

/* --- статус-бар и пилюля состояния --- */
QStatusBar {{ background: {p["bg"]}; color: {p["hint"]}; }}
QStatusBar QLabel {{ background: transparent; }}
QLabel#statePill {{
    border-radius: 10px; padding: 2px 12px; font-weight: 600; color: {p["text"]};
    background: {p["surface"]}; border: 1px solid {p["border"]};
}}
QLabel#statePill[kind="ok"] {{
    color: {p["ok"]}; background: {_rgba(p["ok"], 0.14)};
    border-color: {_rgba(p["ok"], 0.5)};
}}
QLabel#statePill[kind="warn"] {{
    color: {p["warn"]}; background: {_rgba(p["warn"], 0.14)};
    border-color: {_rgba(p["warn"], 0.5)};
}}
QLabel#statePill[kind="danger"] {{
    color: {p["danger"]}; background: {_rgba(p["danger"], 0.16)};
    border-color: {_rgba(p["danger"], 0.6)};
}}

/* --- меню --- */
QMenu {{
    background: {p["surface"]}; color: {p["text"]};
    border: 1px solid {p["border"]}; padding: 4px;
}}
QMenu::item {{ padding: 5px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background: {_rgba(p["accent"], 0.35)}; }}
QMenu::separator {{ height: 1px; background: {p["border"]}; margin: 4px 6px; }}

/* --- скроллбары тоньше --- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p["border"]}; border-radius: 4px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p["hint"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {p["border"]}; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p["hint"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def _palette(dark: bool) -> QPalette:
    """QPalette под Fusion: заполняет то, что QSS не покрывает (спинбоксы и пр.)."""
    p = _DARK if dark else _LIGHT
    pal = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "bg"),
        (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "bg"),
        (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.ButtonText, "text"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.Highlight, "accent"),
        (QPalette.ColorRole.HighlightedText, "on_accent"),
        (QPalette.ColorRole.Link, "accent"),
        (QPalette.ColorRole.PlaceholderText, "hint"),
    ):
        pal.setColor(role, QColor(p[key]))
    disabled = QColor(p["text"])
    disabled.setAlpha(110)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    return pal


def apply_theme(app: QApplication, dark: bool) -> None:
    """Fusion + палитра + QSS. Вызывается в main.py до окна и при смене темы.

    Все виджеты живут на QSS/palette — гибридный вид windowsvista исчезает.
    """
    app.setStyle("Fusion")
    app.setPalette(_palette(dark))
    app.setStyleSheet(qss(dark))


__all__ = [
    "ACCENT_BY_KIND",
    "DEFAULT_THEME",
    "SERIES_FREQ",
    "SERIES_POWER",
    "SERIES_TEMP",
    "THEMES",
    "apply_theme",
    "load_theme",
    "qss",
    "save_theme",
]
