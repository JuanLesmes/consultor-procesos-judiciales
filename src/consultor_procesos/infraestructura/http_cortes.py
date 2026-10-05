"""Solicitante HTTP cortés compartido por todas las fuentes de la Rama Judicial.

Cada solicitud pasa, en este orden, por: cortacircuito -> presupuesto diario -> limitador
de tasa -> HTTP. Los errores transitorios (429, 5xx, tiempo de espera) se reintentan con
retroceso exponencial respetando `Retry-After`. Los errores definitivos (4xx) no se
reintentan; para el cortacircuito cuentan como respuesta sana (la fuente funciona), salvo
401 y 403, que pueden indicar un bloqueo y por eso cuentan como fallo.

Toda solicitud que el cortacircuito deja pasar termina informándole el resultado (éxito,
fallo o "sin conclusión"), también cuando sale por una excepción. Así la sonda del estado
SEMIABIERTO nunca queda tomada y el circuito no se bloquea hasta reiniciar el programa.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any

import httpx

from ..dominio.errores import CircuitoAbierto, ErrorFuente, FuenteNoDisponible
from .cortesia import Cortacircuito, Dormir, LimitadorTasa, PoliticaReintentos, PresupuestoDiario, interpretar_retry_after

log = logging.getLogger(__name__)

# 4xx que no se reintentan pero sí cuentan como fallo para el cortacircuito: pueden ser un bloqueo.
CODIGOS_POSIBLE_BLOQUEO = frozenset({401, 403})


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
                restantes = self.cortacircuito.segundos_restantes()
                cuando = f"se reanuda en {restantes:.0f} s" if restantes > 0 else "hay una consulta de prueba en curso"
                raise CircuitoAbierto(f"Cortacircuito de {self.nombre} abierto por fallos consecutivos; {cuando}.")
            codigo, retry_after, causa, respuesta = self._intentar(ruta, params, cabeceras, intento)
            if respuesta is not None:
                return respuesta
            if not self.reintentos.debe_reintentar(intento, codigo, retry_after):
                detalle = f"código HTTP {codigo}" if codigo is not None else f"error de transporte ({causa})"
                raise FuenteNoDisponible(
                    f"La fuente {self.nombre} no respondió correctamente tras {intento} intento(s): {detalle}.",
                    codigo=codigo,
                ) from causa
            espera = self.reintentos.calcular_espera(intento, retry_after)
            log.info("[%s] Reintentando %s en %.1f s", self.nombre, ruta, espera)
            self._dormir(espera)

    def _intentar(
        self, ruta: str, params: dict[str, Any] | None, cabeceras: dict[str, str] | None, intento: int
    ) -> tuple[int | None, float | None, Exception | None, httpx.Response | None]:
        """Un intento ya autorizado por el cortacircuito. Siempre le informa el resultado.

        Devuelve (código, retry_after, causa, respuesta); `respuesta` solo viene en un éxito.
        Un error definitivo (4xx no reintentable) se lanza como `ErrorFuente`.
        """
        resuelto = False
        try:
            if self.presupuesto is not None:
                self.presupuesto.consumir()
            self.limitador.esperar_turno()
            self.solicitudes_realizadas += 1
            try:
                respuesta = self.http.get(ruta, params=params, headers=cabeceras)
            except httpx.TransportError as exc:
                log.warning("[%s] Error de transporte consultando %s: %s", self.nombre, ruta, exc)
                self.cortacircuito.registrar_fallo()
                resuelto = True
                return None, None, exc, None
            codigo = respuesta.status_code
            if codigo < 400:
                self.cortacircuito.registrar_exito()
                resuelto = True
                return codigo, None, None, respuesta
            retry_after = interpretar_retry_after(respuesta.headers.get("Retry-After"))
            if not self.reintentos.es_reintentable(codigo):
                if codigo in CODIGOS_POSIBLE_BLOQUEO:
                    log.warning("[%s] Respuesta %s consultando %s: posible bloqueo.", self.nombre, codigo, ruta)
                    self.cortacircuito.registrar_fallo()
                else:
                    self.cortacircuito.registrar_exito()
                resuelto = True
                raise ErrorFuente(self.mensaje_error(respuesta), codigo=codigo)
            log.warning(
                "[%s] Respuesta %s consultando %s (intento %d/%d)",
                self.nombre, codigo, ruta, intento, self.reintentos.intentos_max,
            )
            self.cortacircuito.registrar_fallo()
            resuelto = True
            return codigo, retry_after, None, None
        finally:
            if not resuelto:
                # Presupuesto agotado u otra excepción antes de saber nada de la fuente.
                self.cortacircuito.liberar_sonda()

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


def texto_ascii(texto: str) -> str:
    """Las cabeceras HTTP deben ser ASCII: un User-Agent con tildes haría fallar toda solicitud."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def cabeceras_predeterminadas(agente_usuario: str, aceptar: str = "application/json") -> dict[str, str]:
    return {"User-Agent": texto_ascii(agente_usuario), "Accept": aceptar, "Accept-Language": "es-CO,es;q=0.9"}


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
