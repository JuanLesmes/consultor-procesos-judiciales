"""Cliente cortés para el portal Publicaciones Procesales de la Rama Judicial.

Cada consulta filtrada devuelve una página HTML cercana a 1 MB (incluye el catálogo completo
de despachos), así que este cliente usa un ritmo más lento que el de la CPNU y la aplicación
revisa cada despacho a lo sumo una vez cada varias horas.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any
from urllib.parse import urljoin

import httpx

from ...dominio.modelos import PaginaPublicaciones
from ...enlaces import INSTANCIA_PORTLET_PUBLICACIONES, PORTLET_PUBLICACIONES, RUTA_PUBLICACIONES_INICIO, URL_PUBLICACIONES
from ...infraestructura.cortesia import Cortacircuito, Dormir, LimitadorTasa, PoliticaReintentos, PresupuestoDiario
from ...infraestructura.http_cortes import SolicitanteCortes, cabeceras_predeterminadas, preparar_cliente_http
from .analizador import analizar_lista


class ClientePublicaciones:
    """Implementa el puerto `FuentePublicaciones`."""

    nombre = "Publicaciones Procesales"

    def __init__(
        self,
        *,
        agente_usuario: str,
        limitador: LimitadorTasa | None = None,
        reintentos: PoliticaReintentos | None = None,
        cortacircuito: Cortacircuito | None = None,
        presupuesto: PresupuestoDiario | None = None,
        url_base: str = URL_PUBLICACIONES,
        instancia_portlet: str = INSTANCIA_PORTLET_PUBLICACIONES,
        tiempo_espera: float = 90.0,
        cliente_http: httpx.Client | None = None,
        dormir: Dormir = time.sleep,
    ) -> None:
        self._url_base = url_base
        self._instancia = instancia_portlet
        self._http, propio = preparar_cliente_http(
            cliente_http, url_base, tiempo_espera, cabeceras_predeterminadas(agente_usuario, aceptar="text/html,*/*;q=0.8")
        )
        self._solicitante = SolicitanteCortes(
            nombre=self.nombre,
            cliente_http=self._http,
            limitador=limitador if limitador is not None else LimitadorTasa(4, rafaga=1),
            reintentos=reintentos,
            cortacircuito=cortacircuito,
            presupuesto=presupuesto,
            dormir=dormir,
            cerrar_cliente=propio,
        )

    @property
    def solicitudes_realizadas(self) -> int:
        return self._solicitante.solicitudes_realizadas

    @property
    def presupuesto(self) -> PresupuestoDiario | None:
        return self._solicitante.presupuesto

    def cerrar(self) -> None:
        self._solicitante.cerrar()

    def __enter__(self) -> "ClientePublicaciones":
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()

    # --- puerto FuentePublicaciones ---------------------------------------------------

    def parametros(
        self, despacho_codigo: str, id_estructura: int, desde: date, hasta: date, pagina: int, por_pagina: int
    ) -> dict[str, Any]:
        """Los mismos parámetros que construye el propio portal al pulsar "Buscar"."""
        ns = f"_{PORTLET_PUBLICACIONES}_INSTANCE_{self._instancia}_"
        return {
            "p_p_id": f"{PORTLET_PUBLICACIONES}_INSTANCE_{self._instancia}",
            "p_p_lifecycle": "0",
            "p_p_state": "normal",
            "p_p_mode": "view",
            ns + "idStructure": str(int(id_estructura)),
            ns + "idDespacho": despacho_codigo,
            ns + "idEspecialidad": "",
            ns + "idEntidad": "",
            ns + "idDepto": "",
            ns + "idDeptoIdCategory": "",
            ns + "idMuni": "",
            ns + "fechaInicio": desde.isoformat(),
            ns + "fechaFin": hasta.isoformat(),
            ns + "verTotales": "false",
            ns + "action": "filterStructures",
            ns + "delta": str(int(por_pagina)),
            ns + "resetCur": "false",
            ns + "cur": str(int(pagina)),
        }

    def listar_publicaciones(
        self,
        despacho_codigo: str,
        id_estructura: int,
        desde: date,
        hasta: date,
        pagina: int = 1,
        por_pagina: int = 75,
    ) -> PaginaPublicaciones:
        respuesta = self._solicitante.get(
            RUTA_PUBLICACIONES_INICIO,
            self.parametros(despacho_codigo, id_estructura, desde, hasta, pagina, por_pagina),
        )
        return analizar_lista(
            respuesta.text, id_estructura=id_estructura, pagina=pagina, por_pagina=por_pagina, url_base=self._url_base
        )

    def descargar(self, url: str) -> bytes:
        destino = urljoin(self._url_base + "/", url)
        respuesta = self._solicitante.get(destino, cabeceras={"Accept": "*/*"})
        return respuesta.content
