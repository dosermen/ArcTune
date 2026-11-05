"""Локализация ArcTune: русский (по умолчанию) и английский.

Язык хранится в простом файле ``~/.arctune/language`` (``"ru"``/``"en"``), а не
в QSettings: CLI-exe собирается без PySide6 (см. packaging/arctune.spec), где Qt
недоступен. GUI читает и пишет тот же файл, поэтому переключатель «Вид → Язык»
одинаково действует на окно и на командную строку.

Переключение применяется при следующем запуске: все подписи создаются при
построении виджетов и парсера, поэтому окно нужно перезапустить — так же, как
масштаб, который задаётся через QT_SCALE_FACTOR.

Переводятся пользовательские подписи GUI, меню, графики, общий диалог
подтверждения записи, справка argparse и сообщения верхнего уровня CLI.
Технические детали журнала и внутренние сообщения об ошибках намеренно могут
оставаться на русском.
"""

from __future__ import annotations

import os
from pathlib import Path

#: код русского языка (по умолчанию)
LANG_RU = "ru"
#: код английского языка
LANG_EN = "en"

#: перечень поддерживаемых языков
LANGUAGES: tuple[str, ...] = (LANG_RU, LANG_EN)

#: человекочитаемые имена для меню/CLI
LANGUAGE_LABELS: dict[str, str] = {LANG_RU: "Русский", LANG_EN: "English"}

_DEFAULT = LANG_RU
#: язык текущего процесса (переопределяется set_language, иначе — из файла)
_current: str | None = None


def _language_file() -> Path:
    """Путь к файлу с языком (``~/.arctune/language``)."""
    return Path.home() / ".arctune" / "language"


def load_language() -> str:
    """Читает сохранённый язык; недоступен/некорректен → русский.

    Приоритет: переменная окружения ``ARCTUNE_LANG`` (удобна для тестов и
    переопределения) → файл → язык по умолчанию.

    Returns:
        Код языка (``"ru"``/``"en"``).
    """
    env = os.environ.get("ARCTUNE_LANG", "").strip().lower()
    if env in LANGUAGES:
        return env
    try:
        raw = _language_file().read_text(encoding="utf-8").strip().lower()
    except OSError:
        return _DEFAULT
    return raw if raw in LANGUAGES else _DEFAULT


def save_language(lang: str) -> None:
    """Сохраняет язык в файл (GUI-переключатель); применяется после перезапуска.

    Args:
        lang: Код языка (``"ru"``/``"en"``); прочие значения приводятся к русскому.
    """
    if lang not in LANGUAGES:
        lang = _DEFAULT
    path = _language_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lang, encoding="utf-8")


def current_language() -> str:
    """Язык текущего процесса (кэшируется после первого чтения)."""
    global _current
    if _current is None:
        _current = load_language()
    return _current


def set_language(lang: str) -> None:
    """Явно задаёт язык процесса (используется тестами и переключателем)."""
    global _current
    _current = lang if lang in LANGUAGES else _DEFAULT


def tr(ru: str, en: str | None) -> str:
    """Возвращает строку на текущем языке.

    Args:
        ru: Русский вариант (язык по умолчанию).
        en: Английский вариант; ``None`` — строка не переводится (технические
            детали остаются русскими независимо от выбранного языка).

    Returns:
        ``en`` для английского (если задан), иначе ``ru``.
    """
    if current_language() == LANG_EN and en is not None:
        return en
    return ru


__all__ = [
    "LANGUAGES",
    "LANGUAGE_LABELS",
    "LANG_EN",
    "LANG_RU",
    "current_language",
    "load_language",
    "save_language",
    "set_language",
    "tr",
]
