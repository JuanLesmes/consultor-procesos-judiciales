"""Notificador por webhook: envía un POST JSON a la URL configurada (Slack, Teams, n8n, etc.)."""

from __future__ import annotations

import httpx

from ...dominio.modelos import EventoNovedades
from .formato import formatear_evento, novedades_a_mostrar, serializar_coincidencia, serializar_novedad


class NotificadorWebhook:
    def __init__(
        self,
        url: str,
        cliente_http: httpx.Client | None = None,
        tiempo_espera: float = 15.0,
        solo_autos: bool = False,
    ) -> None:
        if not url:
            raise ValueError("El notificador webhook necesita una URL.")
        self._url = url
        self._http = cliente_http or httpx.Client(timeout=tiempo_espera)
        self._solo_autos = solo_autos

    def construir_carga(self, evento: EventoNovedades) -> dict | None:
        novedades = novedades_a_mostrar(evento, self._solo_autos)
        if not novedades and not evento.publicaciones:
            return None
        return {
            "radicado": evento.radicado,
            "alias": evento.alias,
            "despacho": evento.despacho,
            "momento": evento.momento.isoformat(),
            "linea_base": evento.es_linea_base,
            "total_novedades": len(evento.novedades),
            "total_autos": len(evento.autos),
            "text": formatear_evento(evento, solo_autos=self._solo_autos),
            "novedades": [serializar_novedad(evento, n) for n in novedades],
            "total_publicaciones": len(evento.publicaciones),
            "publicaciones": [serializar_coincidencia(c) for c in evento.publicaciones],
        }

    def notificar(self, evento: EventoNovedades) -> None:
        carga = self.construir_carga(evento)
        if carga is None:
            return
        respuesta = self._http.post(self._url, json=carga)
        respuesta.raise_for_status()
