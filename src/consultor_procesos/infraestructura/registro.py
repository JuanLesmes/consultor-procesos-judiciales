"""Configuración del registro (logging) de la aplicación."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configurar_registro(nivel: str = "INFO", archivo: str | Path | None = None) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))
    for manejador in list(raiz.handlers):
        raiz.removeHandler(manejador)
    consola = logging.StreamHandler(sys.stderr)
    consola.setFormatter(logging.Formatter(FORMATO))
    raiz.addHandler(consola)
    if archivo:
        fichero = logging.FileHandler(archivo, encoding="utf-8")
        fichero.setFormatter(logging.Formatter(FORMATO))
        raiz.addHandler(fichero)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
