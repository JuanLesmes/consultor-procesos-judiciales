"""Fixtures compartidas. Las utilidades viven en `apoyo.py` para poder importarlas explícitamente."""

from __future__ import annotations

import pytest

from apoyo import FuenteFalsa, NotificadorRegistro, RelojFalso


@pytest.fixture
def reloj() -> RelojFalso:
    return RelojFalso()


@pytest.fixture
def fuente() -> FuenteFalsa:
    return FuenteFalsa()


@pytest.fixture
def notificador() -> NotificadorRegistro:
    return NotificadorRegistro()
