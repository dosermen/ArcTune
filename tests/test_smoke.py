"""Тесты: smoke-тесты loader и проверка, что IGCL.dll находится в системе.

Без железа пропускаются. Метка hardware — для тестов, требующих реального Arc.
"""

from __future__ import annotations

import pytest

from arctuner.igcl.loader import IgclLoader, IgclNotFoundError


def test_find_dll() -> None:
    """IGCL.dll должна найтись при установленном Intel Arc драйвере."""
    try:
        loader = IgclLoader()
    except IgclNotFoundError:
        pytest.skip("IGCL.dll не найдена (нет Intel Arc драйвера)")
    assert loader.path.exists()


def test_healthcheck_reports_required_functions() -> None:
    try:
        loader = IgclLoader()
    except IgclNotFoundError:
        pytest.skip("IGCL.dll не найдена")
    info = loader.healthcheck()
    assert "missing_required" in info
    # Ключевые функции разгона должны быть в современном драйвере.
    for fn in ("ctlPowerTelemetryGet", "ctlOverclockGetProperties"):
        assert loader.has(fn), f"{fn} отсутствует — драйвер слишком старый?"
