"""Solicitante HTTP cortés compartido por todas las fuentes de la Rama Judicial.

Cada solicitud pasa, en este orden, por: cortacircuito -> presupuesto diario -> limitador
de tasa -> HTTP. Los errores transitorios (429, 5xx, tiempo de espera) se reintentan con
retroceso exponencial respetando `Retry-After`; los errores definitivos (4xx) no se
reintentan ni penalizan al cortacircuito, porque indican que la fuente sí funciona.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..dominio.errores import CircuitoAbierto, ErrorFuente, FuenteNoDisponible
from .cortesia import Cortacircuito, Dormir, LimitadorTasa, PoliticaReintentos, PresupuestoDiario, interpretar_retry_after

log = logging.getLogger(__name__)


class SolicitanteCortes:
    def __init__(
        self,
        *,
        nombre: str,
        cliente_http: httpx.Client,
        limitador: LimitadorTasa | None = None,
        reintentos: PoliticaReintentos | None = None,
        cortacircuito: Cortacircuito | None = None,
        presupuesto: PresupuestoDiario | None = None,
        dormir: Dormir = time.sleep,
        cerrar_cliente: bool = True,
    ) -> None:
        self.nombre = nombre
        self.http = cliente_http
        self.limitador = limitador if limitador is not None else LimitadorTasa(12, rafaga=1)
        self.reintentos = reintentos if reintentos is not None else PoliticaReintentos()
        self.cortacircuito = cortacircuito if cortacircuito is not None else Cortacircuito()
        self.presupuesto = presupuesto
        self._dormir = dormir
        self._cerrar_cliente = cerrar_cliente
        self.solicitudes_realizadas = 0

    def cerrar(self) -> None:
        if self._cerrar_cliente:
            self.http.close()

    def get(
        self, ruta: str, params: dict[str, Any] | None = None, cabeceras: dict[str, str] | None = None
    ) -> httpx.Response:
        """Ejecuta un GET aplicando cortacircuito, presupuesto, limitador y reintentos."""
        intento = 0
        while True:
            intento += 1
            if not self.cortacircuito.permitir():
                raise CircuitoAbierto(
                    f"Cortacircuito de {self.nombre} abierto por fallos consecutivos; "
                    f"se reanuda en {self.cortacircuito.segundos_restantes():.0f} s."
                )
            if self.presupuesto is not None:
                self.presupuesto.consumir()
            self.limitador.esperar_turno()
            self.solicitudes_realizadas += 1

            codigo: int | None = None
            retry_after: float | None = None
            causa: Exception | None = None
            try:
                respuesta = self.http.get(ruta, params=params, headers=cabeceras)
            except httpx.TransportError as exc:
                causa = exc
                log.warning("[%s] Error de transporte consultando %s: %s", self.nombre, ruta, exc)
            else:
                codigo = respuesta.status_code
                if codigo < 400:
                    self.cortacircuito.registrar_exito()
                    return respuesta
                retry_after = interpretar_retry_after(respuesta.headers.get("Retry-After"))
                if not self.reintentos.es_reintentable(codigo):
                    raise ErrorFuente(self.mensaje_error(respuesta), codigo=codigo)
                log.warning(
                    "[%s] Respuesta %s consultando %s (intento %d/%d)",
                    self.nombre, codigo, ruta, intento, self.reintentos.intentos_max,
                )

            self.cortacircuito.registrar_fallo()
            if not self.reintentos.debe_reintentar(intento, codigo, retry_after):
                detalle = f"código HTTP {codigo}" if codigo is not None else f"error de transporte ({causa})"
                raise FuenteNoDisponible(
                    f"La fuente {self.nombre} no respondió correctamente tras {intento} intento(s): {detalle}.",
                    codigo=codigo,
                ) from causa
            espera = self.reintentos.calcular_espera(intento, retry_after)
            log.info("[%s] Reintentando %s en %.1f s", self.nombre, ruta, espera)
            self._dormir(espera)

    def get_json(self, ruta: str, params: dict[str, Any] | None = None) -> Any:
        respuesta = self.get(ruta, params)
        try:
            return respuesta.json()
        except ValueError as exc:
            raise ErrorFuente(f"La respuesta de {ruta} no es JSON válido.", codigo=respuesta.status_code) from exc

    @staticmethod
    def mensaje_error(respuesta: httpx.Response) -> str:
        try:
            cuerpo = respuesta.json()
        except ValueError:
            cuerpo = None
        if isinstance(cuerpo, dict):
            for clave in ("Message", "message", "mensaje", "error"):
                if cuerpo.get(clave):
                    return f"HTTP {respuesta.status_code}: {cuerpo[clave]}"
        texto = respuesta.text.strip()
        return f"HTTP {respuesta.status_code}: {texto[:200] or respuesta.reason_phrase}"


def cabeceras_predeterminadas(agente_usuario: str, aceptar: str = "application/json") -> dict[str, str]:
    return {"User-Agent": agente_usuario, "Accept": aceptar, "Accept-Language": "es-CO,es;q=0.9"}


def preparar_cliente_http(
    cliente_http: httpx.Client | None, url_base: str, tiempo_espera: float, cabeceras: dict[str, str]
) -> tuple[httpx.Client, bool]:
    """Crea el cliente httpx o completa las cabeceras de uno inyectado. Devuelve (cliente, es_propio)."""
    if cliente_http is None:
        return httpx.Client(base_url=url_base, timeout=tiempo_espera, headers=cabeceras), True
    # Cliente inyectado: solo se reemplazan los valores por defecto de httpx,
    # nunca una identificación que el llamador haya definido a propósito.
    actuales = cliente_http.headers
    if actuales.get("User-Agent", "").startswith("python-httpx"):
        actuales["User-Agent"] = cabeceras["User-Agent"]
    if actuales.get("Accept", "*/*") == "*/*":
        actuales["Accept"] = cabeceras["Accept"]
    actuales.setdefault("Accept-Language", cabeceras["Accept-Language"])
    return cliente_http, False
