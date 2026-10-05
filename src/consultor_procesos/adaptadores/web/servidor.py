"""Servidor web local (biblioteca estándar) que expone la vigilancia como una pequeña aplicación.

Diseño:

* `AplicacionWeb` contiene los endpoints como funciones puras sobre `(método, ruta, consulta,
  cuerpo)` y devuelve `(estado, contenido, tipo, cabeceras)`. Se prueba sin abrir puertos.
* `ManejadorHTTP` y `ServidorWeb` son el transporte: `ThreadingHTTPServer` en un hilo.
* Un único candado (`_candado_fuente`) garantiza que hacia la Rama Judicial haya como
  máximo una operación en curso, sea la verificación en segundo plano, la vigilancia
  periódica o una consulta puntual. La cortesía del cliente HTTP se mantiene intacta.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from collections.abc import Callable
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from ... import __version__
from ...aplicacion.horario import Horario
from ...aplicacion.planificador import Planificador
from ...aplicacion.servicio_vigilancia import ServicioVigilancia
from ...dominio.errores import (
    DocumentoNoEncontrado,
    ErrorConsultor,
    FuenteNoDisponible,
    PresupuestoAgotado,
    ProcesoNoEncontrado,
    RadicadoInvalido,
)
from ...dominio.modelos import Novedad, ProcesoVigilado, ResultadoVerificacion
from ...dominio.puertos import Repositorio
from ...dominio.reglas import validar_radicado
from ...enlaces import URL_PORTAL_CONSULTA, url_origen_actuaciones, url_origen_ficha
from ...infraestructura.cortesia import PresupuestoDiario
from ..notificacion.formato import (
    serializar_actuacion,
    serializar_coincidencia,
    serializar_consulta,
    serializar_documento,
    serializar_publicacion,
)
from .visor import renderizar_error, renderizar_visor

log = logging.getLogger(__name__)

DIRECTORIO_ESTATICO = Path(__file__).parent / "estatico"
Respuesta = tuple[int, Any, str, dict[str, str]]
Manejador = Callable[..., Any]


class ErrorHTTP(Exception):
    def __init__(self, estado: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje


class Ocupado(ErrorHTTP):
    def __init__(self, mensaje: str = "Hay una operación hacia la fuente en curso; intente de nuevo en unos segundos.") -> None:
        super().__init__(HTTPStatus.CONFLICT, mensaje)


def _iso(valor: datetime | None) -> str | None:
    return valor.isoformat(timespec="seconds") if valor else None


class TrabajoVerificacion:
    """Estado observable de la verificación que corre en segundo plano."""

    def __init__(self) -> None:
        self._candado = threading.Lock()
        self.en_curso = False
        self.iniciado_en: datetime | None = None
        self.terminado_en: datetime | None = None
        self.resumen: list[dict] | None = None
        self.publicaciones: list[dict] | None = None
        self.error: str | None = None
        self.origen: str = ""

    def iniciar(self, origen: str, momento: datetime) -> None:
        with self._candado:
            self.en_curso = True
            self.iniciado_en = momento
            self.terminado_en = None
            self.resumen = None
            self.publicaciones = None
            self.error = None
            self.origen = origen

    def terminar(
        self,
        momento: datetime,
        resumen: list[dict] | None = None,
        error: str | None = None,
        publicaciones: list[dict] | None = None,
    ) -> None:
        with self._candado:
            self.en_curso = False
            self.terminado_en = momento
            self.resumen = resumen
            self.publicaciones = publicaciones
            self.error = error

    def a_dict(self) -> dict:
        with self._candado:
            return {
                "en_curso": self.en_curso,
                "origen": self.origen,
                "iniciado_en": _iso(self.iniciado_en),
                "terminado_en": _iso(self.terminado_en),
                "resumen": self.resumen,
                "publicaciones": self.publicaciones,
                "error": self.error,
            }


class Vigilante:
    """Ejecuta el planificador en un hilo y permite arrancarlo y detenerlo desde la interfaz."""

    def __init__(self, fabrica: Callable[[Callable[[], None]], Planificador], ciclo: Callable[[], None]) -> None:
        self._fabrica = fabrica
        self._ciclo = ciclo
        self._planificador: Planificador | None = None
        self._hilo: threading.Thread | None = None
        self._candado = threading.Lock()

    @property
    def activo(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def iniciar(self) -> bool:
        with self._candado:
            if self.activo:
                return False
            self._planificador = self._fabrica(self._ciclo)
            self._hilo = threading.Thread(target=self._planificador.ejecutar, name="vigilante", daemon=True)
            self._hilo.start()
            return True

    def detener(self, espera: float = 5.0) -> bool:
        with self._candado:
            if not self.activo or self._planificador is None:
                return False
            self._planificador.detener()
            assert self._hilo is not None
            self._hilo.join(timeout=espera)
            return True

    def a_dict(self) -> dict:
        planificador = self._planificador
        return {
            "disponible": True,
            "activo": self.activo,
            "ciclos": planificador.ciclos_ejecutados if planificador else 0,
            "proxima_ejecucion": _iso(getattr(planificador, "proxima_ejecucion", None)) if self.activo else None,
        }


class AplicacionWeb:
    def __init__(
        self,
        servicio: ServicioVigilancia,
        repositorio: Repositorio,
        fabrica_planificador: Callable[[Callable[[], None]], Planificador] | None = None,
        presupuesto: PresupuestoDiario | None = None,
        segundos_actualizacion: int = 30,
        ahora: Callable[[], datetime] = datetime.now,
        directorio_estatico: Path = DIRECTORIO_ESTATICO,
        horario: Horario | None = None,
    ) -> None:
        self._servicio = servicio
        self._repositorio = repositorio
        self._presupuesto = presupuesto
        self._segundos_actualizacion = segundos_actualizacion
        self._ahora = ahora
        self._directorio_estatico = directorio_estatico
        self._horario = horario
        self._candado_fuente = threading.Lock()
        self.trabajo = TrabajoVerificacion()
        self.vigilante = Vigilante(fabrica_planificador, self._ciclo_vigilante) if fabrica_planificador else None
        self._rutas: list[tuple[str, re.Pattern[str], Manejador]] = [
            ("GET", re.compile(r"/"), self._indice),
            ("GET", re.compile(r"/index\.html"), self._indice),
            ("GET", re.compile(r"/documento/(?P<id_registro>\d+)"), self._pagina_documento),
            ("GET", re.compile(r"/documento/(?P<id_registro>\d+)/(?P<id_documento>\d+)"), self._pagina_documento),
            ("GET", re.compile(r"/api/estado"), self._estado),
            ("GET", re.compile(r"/api/vigilados"), self._listar_vigilados),
            ("POST", re.compile(r"/api/vigilados"), self._agregar_vigilado),
            ("DELETE", re.compile(r"/api/vigilados/(?P<radicado>[0-9 .\-]+)"), self._quitar_vigilado),
            ("GET", re.compile(r"/api/procesos/(?P<radicado>[0-9 .\-]+)"), self._detalle_proceso),
            ("POST", re.compile(r"/api/procesos/(?P<radicado>[0-9 .\-]+)/alias"), self._cambiar_alias),
            ("POST", re.compile(r"/api/verificar"), self._verificar),
            ("GET", re.compile(r"/api/verificar/estado"), self._estado_verificacion),
            ("GET", re.compile(r"/api/novedades"), self._listar_novedades),
            ("GET", re.compile(r"/api/novedades/(?P<id_registro>\d+)"), self._detalle_novedad),
            ("POST", re.compile(r"/api/novedades/(?P<id_registro>\d+)/revisada"), self._marcar_revisada),
            ("GET", re.compile(r"/api/novedades/(?P<id_registro>\d+)/documentos"), self._documentos_novedad),
            ("GET", re.compile(r"/api/documentos/(?P<id_documento>\d+)"), self._descargar_documento),
            ("GET", re.compile(r"/api/consultar/(?P<radicado>[0-9 .\-]+)"), self._consultar),
            ("GET", re.compile(r"/api/historial"), self._historial),
            ("GET", re.compile(r"/api/publicaciones"), self._listar_coincidencias),
            ("GET", re.compile(r"/api/publicaciones/despacho/(?P<codigo>\d{12})"), self._publicaciones_despacho),
            ("POST", re.compile(r"/api/publicaciones/(?P<id_coincidencia>\d+)/revisada"), self._marcar_coincidencia),
            ("POST", re.compile(r"/api/publicaciones/revisar"), self._revisar_publicaciones),
            ("POST", re.compile(r"/api/vigilante/iniciar"), self._iniciar_vigilante),
            ("POST", re.compile(r"/api/vigilante/detener"), self._detener_vigilante),
        ]

    # --- despacho ------------------------------------------------------------------------

    def manejar(self, metodo: str, ruta: str, consulta: dict[str, list[str]], cuerpo: dict | None) -> Respuesta:
        ruta = ruta.rstrip("/") or "/"
        for metodo_ruta, patron, manejador in self._rutas:
            if metodo_ruta != metodo:
                continue
            coincidencia = patron.fullmatch(ruta)
            if coincidencia is None:
                continue
            try:
                resultado = manejador(consulta, cuerpo or {}, **coincidencia.groupdict())
            except ErrorHTTP as exc:
                return self._json(exc.estado, {"error": exc.mensaje})
            except RadicadoInvalido as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except (ProcesoNoEncontrado, DocumentoNoEncontrado) as exc:
                return self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except (FuenteNoDisponible, PresupuestoAgotado) as exc:
                return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            except ErrorConsultor as exc:
                return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - la interfaz debe seguir viva ante cualquier fallo
                log.exception("Error interno atendiendo %s %s", metodo, ruta)
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Error interno: {exc}"})
            if isinstance(resultado, tuple):
                return resultado
            return self._json(HTTPStatus.OK, resultado)
        if any(patron.fullmatch(ruta) for _, patron, _ in self._rutas):
            return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Método no permitido."})
        return self._json(HTTPStatus.NOT_FOUND, {"error": "Ruta no encontrada."})

    @staticmethod
    def _json(estado: int, contenido: Any, cabeceras: dict[str, str] | None = None) -> Respuesta:
        return int(estado), contenido, "application/json; charset=utf-8", cabeceras or {}

    def _con_fuente(self, operacion: Callable[[], Any], espera: float = 0.0) -> Any:
        if not self._candado_fuente.acquire(timeout=espera):
            raise Ocupado()
        try:
            return operacion()
        finally:
            self._candado_fuente.release()

    # --- serialización -------------------------------------------------------------------

    def _alias(self) -> dict[str, str | None]:
        return {v.radicado: v.alias for v in self._repositorio.listar_vigilados(solo_activos=False)}

    def _serializar_novedad(self, novedad: Novedad, alias: dict[str, str | None] | None = None) -> dict:
        alias = alias if alias is not None else self._alias()
        id_registro = novedad.actuacion.id_registro
        return {
            **serializar_actuacion(novedad.actuacion),
            "texto": novedad.actuacion.texto,
            "alias": alias.get(novedad.radicado),
            "despacho": novedad.despacho,
            "es_auto": novedad.es_auto,
            "coincidencias": list(novedad.coincidencias),
            "visto_en": _iso(novedad.visto_en),
            "revisada": novedad.revisada,
            "documentos": [self._serializar_documento(d) for d in novedad.documentos],
            "enlaces": {
                "portal": URL_PORTAL_CONSULTA,
                "detalle": f"/api/novedades/{id_registro}",
                "documentos": f"/api/novedades/{id_registro}/documentos",
                "revisada": f"/api/novedades/{id_registro}/revisada",
                "documento": f"/documento/{id_registro}",
            },
        }

    @staticmethod
    def _serializar_documento(documento) -> dict:
        datos = serializar_documento(documento)
        datos["url_vista"] = f"/api/documentos/{documento.id_documento}"
        datos["url_descarga"] = f"/api/documentos/{documento.id_documento}?descargar=1"
        return datos

    @staticmethod
    def _serializar_vigilado(vigilado: ProcesoVigilado, pendientes: dict[str, dict[str, int]]) -> dict:
        conteo = pendientes.get(vigilado.radicado, {"total": 0, "autos": 0})
        return {
            "radicado": vigilado.radicado,
            "alias": vigilado.alias,
            "titulo": vigilado.titulo,
            "id_proceso": vigilado.id_proceso,
            "despacho": vigilado.despacho,
            "departamento": vigilado.departamento,
            "sujetos": vigilado.sujetos,
            "tipo_proceso": vigilado.tipo_proceso,
            "clase_proceso": vigilado.clase_proceso,
            "ponente": vigilado.ponente,
            "fecha_proceso": vigilado.fecha_proceso.isoformat() if vigilado.fecha_proceso else None,
            "fecha_ultima_actuacion": vigilado.fecha_ultima_actuacion.isoformat() if vigilado.fecha_ultima_actuacion else None,
            "ultima_verificacion": _iso(vigilado.ultima_verificacion),
            "creado_en": _iso(vigilado.creado_en),
            "inicializado": vigilado.inicializado,
            "activo": vigilado.activo,
            "despachos": list(vigilado.codigos_despacho),
            "pendientes": conteo["total"],
            "autos_pendientes": conteo["autos"],
            "enlaces": {
                "portal": URL_PORTAL_CONSULTA,
                # Datos en bruto de la fuente oficial, tal como los recibe el programa (JSON).
                "origen_ficha": url_origen_ficha(vigilado.id_proceso) if vigilado.id_proceso else None,
                "origen_actuaciones": url_origen_actuaciones(vigilado.id_proceso) if vigilado.id_proceso else None,
            },
        }

    @staticmethod
    def _serializar_resultado(resultado: ResultadoVerificacion) -> dict:
        return {
            "radicado": resultado.radicado,
            "estado": resultado.estado.value,
            "momento": _iso(resultado.momento),
            "novedades": len(resultado.novedades),
            "autos": len(resultado.autos),
            "solicitudes": resultado.solicitudes,
            "mensaje": resultado.mensaje,
            "linea_base": resultado.es_linea_base,
            "publicaciones": len(resultado.publicaciones),
        }

    def _pendientes_por_radicado(self) -> dict[str, dict[str, int]]:
        conteo: dict[str, dict[str, int]] = {}
        for novedad in self._repositorio.listar_novedades_recientes(limite=5000, solo_pendientes=True):
            entrada = conteo.setdefault(novedad.radicado, {"total": 0, "autos": 0})
            entrada["total"] += 1
            if novedad.es_auto:
                entrada["autos"] += 1
        return conteo

    # --- página ----------------------------------------------------------------------------

    def _indice(self, consulta: dict, cuerpo: dict) -> Respuesta:
        ruta = self._directorio_estatico / "index.html"
        if not ruta.is_file():
            return HTTPStatus.NOT_FOUND, b"No se encontr\xc3\xb3 la p\xc3\xa1gina de la interfaz.", "text/plain; charset=utf-8", {}
        # no-store: que el navegador nunca reutilice una copia vieja de la interfaz tras una actualización.
        return HTTPStatus.OK, ruta.read_bytes(), "text/html; charset=utf-8", {"Cache-Control": "no-store"}

    # --- estado general -------------------------------------------------------------------

    def _estado(self, consulta: dict, cuerpo: dict) -> dict:
        pendientes = self._pendientes_por_radicado()
        vigilados = [self._serializar_vigilado(v, pendientes) for v in self._repositorio.listar_vigilados(solo_activos=False)]
        return {
            "actualizado_en": _iso(self._ahora()),
            "version": __version__,
            "segundos_actualizacion": self._segundos_actualizacion,
            "portal": URL_PORTAL_CONSULTA,
            "fuente": {
                "nombre": getattr(self._servicio.fuente, "nombre", "?"),
                "solicitudes_sesion": getattr(self._servicio.fuente, "solicitudes_realizadas", 0),
                "ocupada": self._candado_fuente.locked(),
            },
            "presupuesto": (
                {"usado": self._presupuesto.usado(), "maximo": self._presupuesto.maximo} if self._presupuesto else None
            ),
            "pendientes": {
                "total": self._repositorio.contar_pendientes(),
                "autos": self._repositorio.contar_pendientes(solo_autos=True),
                "publicaciones": self._repositorio.contar_coincidencias_pendientes(),
            },
            "publicaciones": {"habilitado": self._servicio.publicaciones is not None},
            "vigilante": {
                **(self.vigilante.a_dict() if self.vigilante else {"disponible": False, "activo": False}),
                "horario": self._horario.a_dict() if self._horario else None,
            },
            "verificacion": self.trabajo.a_dict(),
            "vigilados": vigilados,
        }

    # --- vigilados --------------------------------------------------------------------------

    def _listar_vigilados(self, consulta: dict, cuerpo: dict) -> list[dict]:
        pendientes = self._pendientes_por_radicado()
        return [self._serializar_vigilado(v, pendientes) for v in self._repositorio.listar_vigilados(solo_activos=False)]

    def _agregar_vigilado(self, consulta: dict, cuerpo: dict) -> Respuesta:
        radicado = str(cuerpo.get("radicado", "")).strip()
        alias = str(cuerpo.get("alias", "") or "").strip() or None
        if not radicado:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "Falta el número de radicación.")
        vigilado = self._servicio.agregar(radicado, alias=alias)
        return self._json(HTTPStatus.CREATED, self._serializar_vigilado(vigilado, self._pendientes_por_radicado()))

    def _quitar_vigilado(self, consulta: dict, cuerpo: dict, radicado: str) -> dict:
        return {"radicado": radicado, "eliminado": self._servicio.quitar(radicado)}

    def _vigilado_o_404(self, radicado: str) -> ProcesoVigilado:
        radicado = validar_radicado(radicado)
        vigilado = self._repositorio.obtener_vigilado(radicado)
        if vigilado is None:
            raise ErrorHTTP(HTTPStatus.NOT_FOUND, f"El radicado {radicado} no está en vigilancia.")
        return vigilado

    def _detalle_proceso(self, consulta: dict, cuerpo: dict, radicado: str) -> dict:
        """Segundo nivel de la interfaz: la ficha del proceso con todo su historial local."""
        vigilado = self._vigilado_o_404(radicado)
        try:
            limite = int((consulta.get("limite") or ["20"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'limite' debe ser un número.") from exc
        actuaciones = self._repositorio.listar_actuaciones(vigilado.radicado)
        actuaciones.sort(
            key=lambda n: (n.actuacion.fecha_actuacion or date.min, n.actuacion.consecutivo, n.actuacion.id_registro),
            reverse=True,
        )
        alias = {vigilado.radicado: vigilado.alias}
        publicaciones = self._servicio.publicaciones
        coincidencias = (
            publicaciones.coincidencias_recientes(radicado=vigilado.radicado, limite=200) if publicaciones is not None else []
        )
        verificaciones = self._repositorio.listar_verificaciones(vigilado.radicado, limite=min(max(limite, 1), 200))
        return {
            "proceso": self._serializar_vigilado(vigilado, self._pendientes_por_radicado()),
            "actuaciones": [self._serializar_novedad(n, alias) for n in actuaciones],
            "publicaciones": [self._serializar_coincidencia(c, alias) for c in coincidencias],
            "verificaciones": [{**f, "momento": _iso(f["momento"])} for f in verificaciones],
            "enlaces": {"portal": URL_PORTAL_CONSULTA},
        }

    def _cambiar_alias(self, consulta: dict, cuerpo: dict, radicado: str) -> dict:
        vigilado = self._vigilado_o_404(radicado)
        vigilado.alias = str(cuerpo.get("alias", "") or "").strip() or None
        self._repositorio.guardar_vigilado(vigilado)
        return self._serializar_vigilado(vigilado, self._pendientes_por_radicado())

    # --- verificación -----------------------------------------------------------------------

    def _ejecutar_verificacion(self, radicados: list[str] | None, origen: str, notificar_existentes: bool = False) -> None:
        self.trabajo.iniciar(origen, self._ahora())
        anterior = self._servicio.opciones.notificar_existentes
        try:
            if notificar_existentes:
                self._servicio.opciones.notificar_existentes = True
            resultados = self._servicio.verificar_todos(radicados)
            self.trabajo.terminar(
                self._ahora(),
                resumen=[self._serializar_resultado(r) for r in resultados],
                publicaciones=[self._serializar_resultado_publicaciones(r) for r in self._servicio.ultimos_resultados_publicaciones],
            )
        except Exception as exc:  # noqa: BLE001 - se informa por la interfaz
            log.exception("La verificación falló")
            self.trabajo.terminar(self._ahora(), error=str(exc))
        finally:
            self._servicio.opciones.notificar_existentes = anterior

    def _verificar(self, consulta: dict, cuerpo: dict) -> Respuesta:
        radicados = cuerpo.get("radicados") or None
        if radicados is not None and not isinstance(radicados, list):
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'radicados' debe ser una lista.")
        notificar_existentes = bool(cuerpo.get("notificar_existentes", False))
        if not self._candado_fuente.acquire(blocking=False):
            raise Ocupado()

        def correr() -> None:
            try:
                self._ejecutar_verificacion(radicados, "manual", notificar_existentes)
            finally:
                self._candado_fuente.release()

        threading.Thread(target=correr, name="verificacion-web", daemon=True).start()
        return self._json(HTTPStatus.ACCEPTED, {"iniciado": True, "verificacion": self.trabajo.a_dict()})

    def _estado_verificacion(self, consulta: dict, cuerpo: dict) -> dict:
        return self.trabajo.a_dict()

    def _ciclo_vigilante(self) -> None:
        if not self._candado_fuente.acquire(timeout=600):
            log.warning("El ciclo de vigilancia no pudo tomar el turno hacia la fuente; se reintenta en el próximo ciclo.")
            return
        try:
            self._ejecutar_verificacion(None, "vigilancia")
        finally:
            self._candado_fuente.release()

    def _iniciar_vigilante(self, consulta: dict, cuerpo: dict) -> dict:
        if self.vigilante is None:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "La vigilancia periódica no está disponible en este servidor.")
        self.vigilante.iniciar()
        return self.vigilante.a_dict()

    def _detener_vigilante(self, consulta: dict, cuerpo: dict) -> dict:
        if self.vigilante is None:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "La vigilancia periódica no está disponible en este servidor.")
        self.vigilante.detener()
        return self.vigilante.a_dict()

    # --- novedades ----------------------------------------------------------------------------

    @staticmethod
    def _bandera(consulta: dict, clave: str) -> bool:
        valor = (consulta.get(clave) or [""])[0].strip().lower()
        return valor in ("1", "true", "si", "sí", "on")

    def _listar_novedades(self, consulta: dict, cuerpo: dict) -> list[dict]:
        try:
            limite = int((consulta.get("limite") or ["50"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'limite' debe ser un número.") from exc
        radicado = (consulta.get("radicado") or [""])[0].strip() or None
        novedades = self._servicio.novedades_recientes(
            limite=min(max(limite, 1), 500),
            solo_autos=self._bandera(consulta, "solo_autos"),
            radicado=radicado,
            solo_pendientes=self._bandera(consulta, "pendientes"),
        )
        alias = self._alias()
        return [self._serializar_novedad(n, alias) for n in novedades]

    def _obtener_novedad(self, id_registro: str) -> Novedad:
        novedad = self._servicio.novedad(int(id_registro))
        if novedad is None:
            raise ErrorHTTP(HTTPStatus.NOT_FOUND, f"No hay ninguna actuación registrada con id {id_registro}.")
        return novedad

    def _detalle_novedad(self, consulta: dict, cuerpo: dict, id_registro: str) -> dict:
        return self._serializar_novedad(self._obtener_novedad(id_registro))

    def _marcar_revisada(self, consulta: dict, cuerpo: dict, id_registro: str) -> dict:
        revisada = bool(cuerpo.get("revisada", True))
        if not self._servicio.marcar_revisada(int(id_registro), revisada):
            raise ErrorHTTP(HTTPStatus.NOT_FOUND, f"No hay ninguna actuación registrada con id {id_registro}.")
        return {"id_registro": int(id_registro), "revisada": revisada}

    def _documentos_novedad(self, consulta: dict, cuerpo: dict, id_registro: str) -> list[dict]:
        novedad = self._obtener_novedad(id_registro)
        forzar = self._bandera(consulta, "forzar")
        if not novedad.actuacion.con_documentos and not forzar:
            return [self._serializar_documento(d) for d in novedad.documentos]
        documentos = self._con_fuente(lambda: self._servicio.documentos_de(int(id_registro), forzar=forzar), espera=30)
        return [self._serializar_documento(d) for d in documentos]

    @staticmethod
    def _html(estado: int, contenido: str) -> Respuesta:
        return int(estado), contenido.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-cache"}

    def _pagina_documento(self, consulta: dict, cuerpo: dict, id_registro: str, id_documento: str | None = None) -> Respuesta:
        """Página visor: abre el documento de la actuación (el auto) a pantalla completa, con enlace a la fuente."""
        novedad = self._servicio.novedad(int(id_registro))
        if novedad is None:
            return self._html(HTTPStatus.NOT_FOUND, renderizar_error("No hay ninguna actuación registrada con ese identificador."))
        documentos = list(novedad.documentos)
        aviso: str | None = None
        if not documentos and novedad.actuacion.con_documentos:
            try:
                documentos = self._con_fuente(lambda: self._servicio.documentos_de(novedad.actuacion.id_registro), espera=30)
            except ErrorHTTP as exc:
                aviso = f"No se pudo obtener la lista de documentos: {exc.mensaje}"
            except ErrorConsultor as exc:
                aviso = f"No se pudo obtener la lista de documentos: {exc}"
        seleccionado = None
        if documentos:
            seleccionado = next(
                (d for d in documentos if id_documento is not None and d.id_documento == int(id_documento)), documentos[0]
            )
        vigilado = self._repositorio.obtener_vigilado(novedad.radicado)
        pagina = renderizar_visor(
            novedad,
            documentos,
            seleccionado,
            alias=vigilado.alias if vigilado else None,
            titulo_proceso=vigilado.titulo if vigilado else None,
            aviso=aviso,
            portal=URL_PORTAL_CONSULTA,
        )
        return self._html(HTTPStatus.OK, pagina)

    def _descargar_documento(self, consulta: dict, cuerpo: dict, id_documento: str) -> Respuesta:
        descarga = self._con_fuente(lambda: self._servicio.descargar_documento(int(id_documento)), espera=30)
        disposicion = "attachment" if self._bandera(consulta, "descargar") else "inline"
        nombre_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", descarga.nombre) or f"documento_{id_documento}"
        cabeceras = {
            "Content-Disposition": f"{disposicion}; filename=\"{nombre_ascii}\"; filename*=UTF-8''{quote(descarga.nombre)}",
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        }
        return HTTPStatus.OK, descarga.contenido, descarga.tipo_contenido, cabeceras

    # --- publicaciones procesales --------------------------------------------------------------

    @staticmethod
    def _serializar_resultado_publicaciones(resultado) -> dict:
        return {
            "despacho_codigo": resultado.despacho_codigo,
            "despacho": resultado.despacho,
            "estado": resultado.estado.value,
            "momento": _iso(resultado.momento),
            "nuevas": resultado.nuevas,
            "coincidencias": len(resultado.coincidencias),
            "solicitudes": resultado.solicitudes,
            "mensaje": resultado.mensaje,
        }

    def _servicio_publicaciones(self):
        servicio = self._servicio.publicaciones
        if servicio is None:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "La revisión de publicaciones procesales está desactivada en la configuración.")
        return servicio

    def _serializar_coincidencia(self, coincidencia, alias: dict[str, str | None] | None = None) -> dict:
        alias = alias if alias is not None else self._alias()
        datos = serializar_coincidencia(coincidencia)
        datos["alias"] = alias.get(coincidencia.radicado)
        datos["enlaces"] = {"revisada": f"/api/publicaciones/{coincidencia.id}/revisada"}
        return datos

    def _listar_coincidencias(self, consulta: dict, cuerpo: dict) -> list[dict]:
        servicio = self._servicio_publicaciones()
        try:
            limite = int((consulta.get("limite") or ["50"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'limite' debe ser un número.") from exc
        radicado = (consulta.get("radicado") or [""])[0].strip() or None
        coincidencias = servicio.coincidencias_recientes(
            radicado=radicado, limite=min(max(limite, 1), 500), solo_pendientes=self._bandera(consulta, "pendientes")
        )
        alias = self._alias()
        return [self._serializar_coincidencia(c, alias) for c in coincidencias]

    def _publicaciones_despacho(self, consulta: dict, cuerpo: dict, codigo: str) -> list[dict]:
        servicio = self._servicio_publicaciones()
        try:
            limite = int((consulta.get("limite") or ["50"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'limite' debe ser un número.") from exc
        return [serializar_publicacion(p) for p in servicio.publicaciones_despacho(codigo, limite=min(max(limite, 1), 500))]

    def _marcar_coincidencia(self, consulta: dict, cuerpo: dict, id_coincidencia: str) -> dict:
        servicio = self._servicio_publicaciones()
        revisada = bool(cuerpo.get("revisada", True))
        if not servicio.marcar_revisada(int(id_coincidencia), revisada):
            raise ErrorHTTP(HTTPStatus.NOT_FOUND, f"No hay ninguna coincidencia con id {id_coincidencia}.")
        return {"id": int(id_coincidencia), "revisada": revisada}

    def _revisar_publicaciones(self, consulta: dict, cuerpo: dict) -> Respuesta:
        servicio = self._servicio_publicaciones()
        radicados = cuerpo.get("radicados") or None
        if radicados is not None and not isinstance(radicados, list):
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'radicados' debe ser una lista.")
        if not self._candado_fuente.acquire(blocking=False):
            raise Ocupado()

        def correr() -> None:
            try:
                self.trabajo.iniciar("publicaciones", self._ahora())
                try:
                    vigilados = self._servicio.listar()
                    if radicados:
                        filtro = set(radicados)
                        vigilados = [v for v in vigilados if v.radicado in filtro]
                    resultados = servicio.revisar(vigilados, forzar=True)
                    self.trabajo.terminar(
                        self._ahora(), resumen=[], publicaciones=[self._serializar_resultado_publicaciones(r) for r in resultados]
                    )
                except Exception as exc:  # noqa: BLE001 - se informa por la interfaz
                    log.exception("La revisión de publicaciones falló")
                    self.trabajo.terminar(self._ahora(), error=str(exc))
            finally:
                self._candado_fuente.release()

        threading.Thread(target=correr, name="publicaciones-web", daemon=True).start()
        return self._json(HTTPStatus.ACCEPTED, {"iniciado": True, "verificacion": self.trabajo.a_dict()})

    # --- consulta puntual e historial ---------------------------------------------------------

    def _consultar(self, consulta: dict, cuerpo: dict, radicado: str) -> dict:
        try:
            max_paginas = int((consulta.get("max_paginas") or ["3"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'max_paginas' debe ser un número.") from exc
        incluir_detalle = not self._bandera(consulta, "sin_detalle")
        resultado = self._con_fuente(
            lambda: self._servicio.consultar(radicado, incluir_detalle=incluir_detalle, max_paginas=min(max(max_paginas, 1), 10))
        )
        datos = serializar_consulta(resultado)
        datos["alias"] = self._alias().get(resultado.radicado)
        return datos

    def _historial(self, consulta: dict, cuerpo: dict) -> list[dict]:
        radicado = (consulta.get("radicado") or [""])[0].strip() or None
        try:
            limite = int((consulta.get("limite") or ["30"])[0])
        except ValueError as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "'limite' debe ser un número.") from exc
        filas = self._repositorio.listar_verificaciones(radicado, limite=min(max(limite, 1), 500))
        return [{**f, "momento": _iso(f["momento"])} for f in filas]


class ManejadorHTTP(BaseHTTPRequestHandler):
    aplicacion: AplicacionWeb
    server_version = "ConsultorProcesos/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, formato: str, *args: Any) -> None:  # noqa: N802 - nombre impuesto por la biblioteca
        log.debug("%s - " + formato, self.address_string(), *args)

    def do_GET(self) -> None:  # noqa: N802
        self._despachar("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._despachar("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._despachar("DELETE")

    def _leer_cuerpo(self) -> dict | None:
        longitud = int(self.headers.get("Content-Length") or 0)
        if longitud <= 0:
            return None
        crudo = self.rfile.read(longitud)
        if not crudo.strip():
            return None
        try:
            datos = json.loads(crudo.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, f"El cuerpo no es JSON válido: {exc}") from exc
        if not isinstance(datos, dict):
            raise ErrorHTTP(HTTPStatus.BAD_REQUEST, "El cuerpo debe ser un objeto JSON.")
        return datos

    def _despachar(self, metodo: str) -> None:
        url = urlparse(self.path)
        try:
            cuerpo = self._leer_cuerpo()
        except ErrorHTTP as exc:
            estado, contenido, tipo, cabeceras = AplicacionWeb._json(exc.estado, {"error": exc.mensaje})
        else:
            estado, contenido, tipo, cabeceras = self.aplicacion.manejar(metodo, url.path, parse_qs(url.query), cuerpo)
        if isinstance(contenido, (dict, list)):
            datos = json.dumps(contenido, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(contenido, str):
            datos = contenido.encode("utf-8")
        else:
            datos = bytes(contenido)
        self.send_response(int(estado))
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        for clave, valor in cabeceras.items():
            self.send_header(clave, valor)
        self.end_headers()
        if metodo != "HEAD":
            self.wfile.write(datos)


class ServidorWeb:
    def __init__(self, aplicacion: AplicacionWeb, host: str = "127.0.0.1", puerto: int = 8765) -> None:
        self.aplicacion = aplicacion
        self._host = host
        self._puerto = puerto
        self._servidor: ThreadingHTTPServer | None = None
        self._hilo: threading.Thread | None = None

    @property
    def direccion(self) -> tuple[str, int]:
        if self._servidor is None:
            return self._host, self._puerto
        host, puerto = self._servidor.server_address[:2]
        return str(host), int(puerto)

    @property
    def url(self) -> str:
        host, puerto = self.direccion
        return f"http://{host}:{puerto}/"

    def iniciar(self) -> tuple[str, int]:
        manejador = type("ManejadorHTTPConfigurado", (ManejadorHTTP,), {"aplicacion": self.aplicacion})
        # En Windows SO_REUSEADDR permite que dos procesos escuchen el mismo puerto y las
        # conexiones se repartan al azar entre ellos; se prefiere fallar con "puerto en uso".
        ThreadingHTTPServer.allow_reuse_address = sys.platform != "win32"
        self._servidor = ThreadingHTTPServer((self._host, self._puerto), manejador)
        self._servidor.daemon_threads = True
        self._hilo = threading.Thread(target=self._servidor.serve_forever, name="servidor-web", daemon=True)
        self._hilo.start()
        log.info("Interfaz web disponible en %s", self.url)
        return self.direccion

    def servir_para_siempre(self) -> None:
        if self._hilo is None:
            self.iniciar()
        assert self._hilo is not None
        while self._hilo.is_alive():
            self._hilo.join(timeout=0.5)

    def detener(self) -> None:
        if self.aplicacion.vigilante is not None:
            self.aplicacion.vigilante.detener()
        if self._servidor is not None:
            self._servidor.shutdown()
            self._servidor.server_close()
            self._servidor = None
        self._hilo = None
