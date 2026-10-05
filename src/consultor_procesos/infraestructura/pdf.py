"""Extracción de texto de PDF con pypdf. Devuelve cadena vacía si el archivo no tiene texto (escaneado) o falla."""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)


def extraer_texto_pdf(datos: bytes, max_paginas: int = 80) -> str:
    if not datos or not datos.lstrip().startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependencia declarada en pyproject
        log.warning("pypdf no está instalado; no se puede leer el texto de los PDF.")
        return ""
    try:
        lector = PdfReader(io.BytesIO(datos))
        partes: list[str] = []
        for indice, pagina in enumerate(lector.pages):
            if indice >= max_paginas:
                break
            try:
                partes.append(pagina.extract_text() or "")
            except Exception as exc:  # noqa: BLE001 - una página corrupta no invalida el resto
                log.debug("No se pudo extraer texto de la página %d: %s", indice + 1, exc)
        return "\n".join(partes)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer el PDF: %s", exc)
        return ""
