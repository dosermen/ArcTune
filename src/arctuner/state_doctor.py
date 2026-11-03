"""State Doctor — сверка состояния карты с журналом (read-only).

Чистая композиция готовых модулей:

- :func:`arctuner.tuner.current_values` — фактические значения OC-пунктов
  с карты (V2 GET);
- :class:`arctuner.logbook.Logbook` — ожидаемые значения из журнала изменений;
- версия драйвера из ``ctlGetDeviceProperties`` — хранится в
  ``~/.arctune/driver_version`` и сверяется между сессиями (смена драйвера
  часто и есть причина «тихого» сброса разгона).

Модуль ничего не пишет на карту: единственная запись — служебный файл
версии драйвера в ``~/.arctune/`` (как ``language``/``load_source``).

Сверяются только ключи ``current_values()`` (5 OC-пунктов); поля журнала
вне этих ключей (вентиляторы, маркеры сброса) в сравнение не входят.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Единицы измерения OC-пунктов (для человекочитаемого вывода).
FIELD_UNITS: dict[str, str] = {
    "gpuFrequencyOffset": "МГц",
    "powerLimit": "%",
    "temperatureLimit": "°C",
    "vramMemSpeedLimit": "Гбит/с",
    "gpuVoltageOffset": "%",
}

#: Поля со смещением — в сообщениях показываются со знаком (+160 / -50).
_SIGNED_FIELDS = frozenset({"gpuFrequencyOffset", "gpuVoltageOffset"})

Status = Literal["ok", "drift", "no_log", "unknown"]


@dataclass(frozen=True)
class StateFinding:
    """Один результат сверки поля карты с журналом."""

    field: str
    status: Status
    expected: float | None  # значение из журнала (None — записей нет)
    actual: float | None  # значение с карты (None — не прочиталось)
    message: str  # готовое сообщение по-русски


def format_value(field: str, value: float | None) -> str:
    """Значение поля с единицей измерения (со знаком для offset-полей)."""
    if value is None:
        return "—"
    unit = FIELD_UNITS.get(field, "")
    text = f"{value:+g}" if field in _SIGNED_FIELDS else f"{value:g}"
    return f"{text} {unit}".rstrip()


def compare_state(
    actual: dict[str, float | None],
    expected: dict[str, float | None],
    tolerances: dict[str, float] | None = None,
) -> list[StateFinding]:
    """Сверяет фактические значения с ожидаемыми из журнала.

    Допуск на поле — половина шага ``step`` из ``ctlOverclockGetProperties``:
    драйвер округляет записанное значение до шага, и строгое равенство давало
    бы ложные срабатывания.

    Args:
        actual: значения с карты (ключи ``current_values()``).
        expected: последние значения журнала (``Logbook.latest_values()``).
        tolerances: поля → допуск (по умолчанию 0 — строгое равенство).

    Returns:
        Список находок в порядке ``actual``; поля вне ``actual`` игнорируются.
    """
    tol = tolerances or {}
    findings: list[StateFinding] = []
    for field, act in actual.items():
        exp = expected.get(field)
        unit = FIELD_UNITS.get(field, "")
        if exp is None:
            findings.append(
                StateFinding(
                    field=field,
                    status="no_log",
                    expected=None,
                    actual=act,
                    message=f"{field}: нет записей в журнале (сверять нечего)",
                )
            )
            continue
        if act is None:
            findings.append(
                StateFinding(
                    field=field,
                    status="unknown",
                    expected=exp,
                    actual=None,
                    message=f"{field}: не удалось прочитать текущее значение",
                )
            )
            continue
        limit = tol.get(field, 0.0)
        if abs(act - exp) <= limit:
            findings.append(
                StateFinding(
                    field=field,
                    status="ok",
                    expected=exp,
                    actual=act,
                    message=f"{field}: {format_value(field, act)} — совпадает с журналом",
                )
            )
            continue
        exp_txt = format_value(field, exp)
        act_txt = format_value(field, act)
        tol_txt = f"±{limit:g} {unit}".rstrip()
        findings.append(
            StateFinding(
                field=field,
                status="drift",
                expected=exp,
                actual=act,
                message=(
                    f"{field}: ожидался {exp_txt}, фактически {act_txt} — "
                    f"сброшено драйвером? (допуск {tol_txt})"
                ),
            )
        )
    return findings


def driver_version_path() -> Path:
    """Путь файла с последней замеченной версией драйвера."""
    return Path.home() / ".arctune" / "driver_version"


def check_driver_version(
    current: str, path: Path | None = None
) -> tuple[Literal["saved", "same", "changed"], str | None]:
    """Сверяет текущую версию драйвера с сохранённой между сессиями.

    При первом запуске или смене версии файл перезаписывается; при совпадении
    файл не трогается. Запись возможна только в служебный файл ``~/.arctune/``
    — на карту ничего не пишется.

    Args:
        current: текущая версия (``ExtendedDeviceInfo.driver_version_str``).
        path: файл состояния (по умолчанию ``~/.arctune/driver_version``).

    Returns:
        ``(status, previous)``: ``saved`` — первый запуск (previous=None),
        ``same`` — версия не менялась, ``changed`` — менялась (previous=прежняя).

    Raises:
        OSError: файл состояния не читается/не пишется.
    """
    target = path if path is not None else driver_version_path()
    previous: str | None = None
    if target.exists():
        previous = target.read_text(encoding="utf-8").strip() or None
    status: Literal["saved", "same", "changed"]
    if previous is None:
        status = "saved"
    elif previous == current:
        status = "same"
    else:
        status = "changed"
    if status != "same":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(current + "\n", encoding="utf-8")
    return status, previous
