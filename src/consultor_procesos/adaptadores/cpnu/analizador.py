"""Traducción de las respuestas JSON de la CPNU a modelos del dominio.

Formas observadas en la API (septiembre de 2026):

* Búsqueda: {"tipoConsulta", "procesos": [{idProceso, llaveProceso, fechaUltimaActuacion,
  despacho, departamento, sujetosProcesales, esPrivado, ...}], "paginacion": {...}}
* Detalle:  {idRegProceso, llaveProceso, despacho, ponente, tipoProceso, claseProceso, ...}
* Actuaciones: {"actuaciones": [{idRegActuacion, consActuacion, fechaActuacion, actuacion,
  anotacion, fechaRegistro, conDocumentos, ...}], "paginacion": {cantidadRegistros,
  registrosPagina, cantidadPaginas, pagina}}  (40 por página, más recientes primero)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ...dominio.errores import ErrorFuente
from ...dominio.modelos import Actuacion, DetalleProceso, Documento, PaginaActuaciones, Proceso
from ...dominio.reglas import validar_radicado

__all__ = [
    "validar_radicado",
    "analizar_fecha",
    "analizar_fecha_hora",
    "analizar_proceso",
    "analizar_respuesta_busqueda",
    "analizar_detalle",
    "analizar_actuacion",
    "analizar_pagina_actuaciones",
    "analizar_documento",
    "analizar_documentos",
]


def analizar_fecha_hora(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return datetime.fromisoformat(valor.strip())
    except ValueError:
        return None


def analizar_fecha(valor: Any) -> date | None:
    fecha_hora = analizar_fecha_hora(valor)
    return fecha_hora.date() if fecha_hora else None


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _entero(valor: Any, predeterminado: int = 0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return predeterminado


def _objeto(datos: Any, contexto: str) -> dict:
    if not isinstance(datos, dict):
        raise ErrorFuente(f"Respuesta inesperada de la fuente en {contexto}: se esperaba un objeto JSON.")
    return datos


def analizar_proceso(datos: dict) -> Proceso:
    return Proceso(
        id_proceso=_entero(datos.get("idProceso")),
        radicado=_texto(datos.get("llaveProceso")),
        despacho=_texto(datos.get("despacho")),
        departamento=_texto(datos.get("departamento")),
        sujetos=_texto(datos.get("sujetosProcesales")),
        fecha_proceso=analizar_fecha(datos.get("fechaProceso")),
        fecha_ultima_actuacion=analizar_fecha(datos.get("fechaUltimaActuacion")),
        es_privado=bool(datos.get("esPrivado", False)),
    )


def analizar_respuesta_busqueda(datos: Any) -> list[Proceso]:
    objeto = _objeto(datos, "la búsqueda por radicado")
    procesos = objeto.get("procesos")
    if procesos is None:
        return []
    if not isinstance(procesos, list):
        raise ErrorFuente("Respuesta inesperada de la fuente: 'procesos' no es una lista.")
    return [analizar_proceso(p) for p in procesos if isinstance(p, dict)]


def analizar_detalle(datos: Any, id_proceso: int) -> DetalleProceso:
    objeto = _objeto(datos, "el detalle del proceso")
    return DetalleProceso(
        id_proceso=id_proceso,
        radicado=_texto(objeto.get("llaveProceso")),
        despacho=_texto(objeto.get("despacho")),
        ponente=_texto(objeto.get("ponente")),
        tipo_proceso=_texto(objeto.get("tipoProceso")),
        clase_proceso=_texto(objeto.get("claseProceso")),
        subclase_proceso=_texto(objeto.get("subclaseProceso")),
        recurso=_texto(objeto.get("recurso")),
        ubicacion=_texto(objeto.get("ubicacion")),
        contenido_radicacion=_texto(objeto.get("contenidoRadicacion")),
        es_privado=bool(objeto.get("esPrivado", False)),
        fecha_proceso=analizar_fecha(objeto.get("fechaProceso")),
        ultima_actualizacion=analizar_fecha_hora(objeto.get("ultimaActualizacion")),
        codigo_despacho=_texto(objeto.get("codDespachoCompleto")),
    )


def analizar_actuacion(datos: dict, id_proceso: int | None = None) -> Actuacion:
    return Actuacion(
        id_registro=_entero(datos.get("idRegActuacion")),
        radicado=_texto(datos.get("llaveProceso")),
        consecutivo=_entero(datos.get("consActuacion")),
        actuacion=_texto(datos.get("actuacion")),
        anotacion=_texto(datos.get("anotacion")),
        fecha_actuacion=analizar_fecha(datos.get("fechaActuacion")),
        fecha_registro=analizar_fecha(datos.get("fechaRegistro")),
        fecha_inicial=analizar_fecha(datos.get("fechaInicial")),
        fecha_final=analizar_fecha(datos.get("fechaFinal")),
        con_documentos=bool(datos.get("conDocumentos", False)),
        id_proceso=id_proceso,
    )


def analizar_pagina_actuaciones(datos: Any, id_proceso: int | None = None) -> PaginaActuaciones:
    objeto = _objeto(datos, "las actuaciones")
    lista = objeto.get("actuaciones") or []
    if not isinstance(lista, list):
        raise ErrorFuente("Respuesta inesperada de la fuente: 'actuaciones' no es una lista.")
    actuaciones = tuple(analizar_actuacion(a, id_proceso=id_proceso) for a in lista if isinstance(a, dict))
    paginacion = objeto.get("paginacion")
    if not isinstance(paginacion, dict):
        paginacion = {}
    return PaginaActuaciones(
        actuaciones=actuaciones,
        pagina=max(1, _entero(paginacion.get("pagina"), 1)),
        total_paginas=max(1, _entero(paginacion.get("cantidadPaginas"), 1)),
        total_registros=_entero(paginacion.get("cantidadRegistros"), len(actuaciones)),
        registros_por_pagina=_entero(paginacion.get("registrosPagina"), len(actuaciones)),
    )


def analizar_documento(datos: dict, id_registro: int) -> Documento | None:
    """Documento de una actuación. El portal usa `idRegDocumento` y `nombre`; el resto es opcional."""
    identificador = _entero(datos.get("idRegDocumento", datos.get("idDocumento", datos.get("id"))), 0)
    if not identificador:
        return None
    tamano_bruto = datos.get("tamano", datos.get("tamanio", datos.get("size")))
    tamano = _entero(tamano_bruto, -1) if tamano_bruto is not None else -1
    return Documento(
        id_documento=identificador,
        id_registro=id_registro,
        nombre=_texto(datos.get("nombre") or datos.get("nombreDocumento") or datos.get("nombreArchivo")),
        fecha=analizar_fecha(datos.get("fechaPublicacion") or datos.get("fechaDocumento") or datos.get("fecha")),
        tipo=_texto(datos.get("tipoDocumento") or datos.get("tipo") or datos.get("extension")),
        tamano=tamano if tamano >= 0 else None,
    )


def analizar_documentos(datos: Any, id_registro: int) -> list[Documento]:
    if datos is None:
        return []
    lista = datos
    if isinstance(datos, dict):
        lista = datos.get("documentos") or datos.get("documentosActuacion") or []
    if not isinstance(lista, list):
        raise ErrorFuente("Respuesta inesperada de la fuente: la lista de documentos no es una lista.")
    documentos = (analizar_documento(d, id_registro) for d in lista if isinstance(d, dict))
    return [d for d in documentos if d is not None]
