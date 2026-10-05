"""Notificador que agrega una línea JSON por novedad a un archivo (formato JSON Lines)."""

from __future__ import annotations

import json
from pathlib import Path

from ...dominio.modelos import EventoNovedades
from .formato import novedades_a_mostrar, serializar_evento_publicacion, serializar_novedad


class NotificadorArchivoJSONL:
    def __init__(self, ruta: str | Path, solo_autos: bool = False) -> None:
        self.ruta = Path(ruta)
        self._solo_autos = solo_autos

    def notificar(self, evento: EventoNovedades) -> None:
        novedades = novedades_a_mostrar(evento, self._solo_autos)
        if not novedades and not evento.publicaciones:
            return
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            for novedad in novedades:
                archivo.write(json.dumps(serializar_novedad(evento, novedad), ensure_ascii=False) + "\n")
            for coincidencia in evento.publicaciones:
                archivo.write(json.dumps(serializar_evento_publicacion(evento, coincidencia), ensure_ascii=False) + "\n")
