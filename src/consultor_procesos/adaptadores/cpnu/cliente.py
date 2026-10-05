"""Cliente HTTP cortés para la Consulta de Procesos Nacional Unificada (CPNU).

La política de cortesía (cortacircuito, presupuesto, limitador, reintentos) vive en
`infraestructura.http_cortes.SolicitanteCortes` y es la misma para todas las fuentes.
"""

from __future__ import annotations

import re
import time
from urllib.parse import unquote

import httpx

from ... import __version__
from ...dominio.errores import RespuestaInesperada
from ...dominio.modelos import DescargaDocumento, DetalleProceso, Documento, PaginaActuaciones, Proceso
from ...enlaces import URL_BASE_CPNU
from ...infraestructura.cortesia import Cortacircuito, Dormir, LimitadorTasa, PoliticaReintentos, PresupuestoDiario
from ...infraestructura.http_cortes import SolicitanteCortes, cabeceras_predeterminadas, preparar_cliente_http
from .analizador import (
    analizar_detalle,
    analizar_documentos,
    analizar_pagina_actuaciones,
    analizar_respuesta_busqueda,
    validar_radicado,
)

AGENTE_USUARIO_PREDETERMINADO = (
    f"ConsultorDeProcesos/{__version__} (vigilancia de radicados propios; "
    "configure CONSULTOR_CONTACTO con un correo de contacto)"
)

_RE_NOMBRE_EXTENDIDO = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)")
_RE_NOMBRE = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def nombre_desde_disposicion(valor: str | None) -> str:
    """Extrae el nombre de archivo de una cabecera Content-Disposition, si lo trae."""
    if not valor:
        return ""
    coincidencia = _RE_NOMBRE_EXTENDIDO.search(valor)
    if coincidencia:
        return unquote(coincidencia.group(1).strip())
    coincidencia = _RE_NOMBRE.search(valor)
    return coincidencia.group(1).strip() if coincidencia else ""


class ClienteCPNU:
    """Implementa el puerto `FuenteProcesos` contra la API pública que usa el propio portal."""

    nombre = "CPNU"

    def __init__(
        self,
        *,
        agente_usuario: str = AGENTE_USUARIO_PREDETERMINADO,
        limitador: LimitadorTasa | None = None,
        reintentos: PoliticaReintentos | None = None,
        cortacircuito: Cortacircuito | None = None,
        presupuesto: PresupuestoDiario | None = None,
        url_base: str = URL_BASE_CPNU,
        tiempo_espera: float = 20.0,
        cliente_http: httpx.Client | None = None,
        dormir: Dormir = time.sleep,
    ) -> None:
        self._http, propio = preparar_cliente_http(
            cliente_http, url_base, tiempo_espera, cabeceras_predeterminadas(agente_usuario)
        )
        self._solicitante = SolicitanteCortes(
            nombre=self.nombre,
            cliente_http=self._http,
            limitador=limitador,
            reintentos=reintentos,
            cortacircuito=cortacircuito,
            presupuesto=presupuesto,
            dormir=dormir,
            cerrar_cliente=propio,
        )

    # --- ciclo de vida -----------------------------------------------------------------

    @property
    def solicitudes_realizadas(self) -> int:
        return self._solicitante.solicitudes_realizadas

    @property
    def presupuesto(self) -> PresupuestoDiario | None:
        return self._solicitante.presupuesto

    @property
    def cortacircuito(self) -> Cortacircuito:
        return self._solicitante.cortacircuito

    def cerrar(self) -> None:
        self._solicitante.cerrar()

    def __enter__(self) -> "ClienteCPNU":
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()

    # --- puerto FuenteProcesos --------------------------------------------------------

    def buscar_por_radicado(self, radicado: str, solo_activos: bool = False) -> list[Proceso]:
        radicado = validar_radicado(radicado)
        datos = self._solicitante.get_json(
            "/Procesos/Consulta/NumeroRadicacion",
            {"numero": radicado, "SoloActivos": "true" if solo_activos else "false", "pagina": 1},
        )
        return analizar_respuesta_busqueda(datos)

    def obtener_detalle(self, id_proceso: int) -> DetalleProceso:
        datos = self._solicitante.get_json(f"/Proceso/Detalle/{int(id_proceso)}")
        return analizar_detalle(datos, id_proceso=int(id_proceso))

    def obtener_actuaciones(self, id_proceso: int, pagina: int = 1) -> PaginaActuaciones:
        datos = self._solicitante.get_json(f"/Proceso/Actuaciones/{int(id_proceso)}", {"pagina": int(pagina)})
        return analizar_pagina_actuaciones(datos, id_proceso=int(id_proceso))

    def listar_documentos(self, id_registro: int) -> list[Documento]:
        datos = self._solicitante.get_json(f"/Proceso/DocumentosActuacion/{int(id_registro)}")
        return analizar_documentos(datos, id_registro=int(id_registro))

    def descargar_documento(self, id_documento: int) -> DescargaDocumento:
        ruta = f"/Descarga/DocumentoActuacion/{int(id_documento)}"
        respuesta = self._solicitante.get(ruta, cabeceras={"Accept": "*/*"})
        tipo = respuesta.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        if tipo == "application/json":
            raise RespuestaInesperada(
                f"La fuente devolvió JSON en lugar del archivo para el documento {id_documento}; "
                "el formato de descarga cambió y hay que ajustar el cliente."
            )
        return DescargaDocumento(
            id_documento=int(id_documento),
            contenido=respuesta.content,
            nombre=nombre_desde_disposicion(respuesta.headers.get("Content-Disposition")),
            tipo_contenido=tipo or "application/octet-stream",
        )
