"""Notificador que escribe las novedades en la salida estándar (o en el flujo indicado)."""

from __future__ import annotations

import sys
from typing import TextIO

from ...dominio.modelos import EventoNovedades
from .formato import formatear_evento


class NotificadorConsola:
    def __init__(self, salida: TextIO | None = None, solo_autos: bool = False) -> None:
        self._salida = salida
        self._solo_autos = solo_autos

    def notificar(self, evento: EventoNovedades) -> None:
        texto = formatear_evento(evento, solo_autos=self._solo_autos)
        if not texto:
            return
        destino = self._salida or sys.stdout
        print(texto, file=destino, flush=True)
