"""Utilidades compartidas por las pruebas.

Los diccionarios replican la forma real de la API de la CPNU (observada en septiembre de 2026)
pero con datos ficticios: no contienen nombres ni radicados de personas reales.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime, timedelta

from consultor_procesos.dominio.errores import ErrorFuente
from consultor_procesos.dominio.modelos import (
    Actuacion,
    DescargaDocumento,
    DetalleProceso,
    Documento,
    PaginaActuaciones,
    Proceso,
)

RADICADO = "11001400300120240012345"
RADICADO_2 = "05001310300320230067890"
ID_PROCESO = 12345678
ID_PROCESO_2 = 87654321
DESPACHO = "JUZGADO 001 CIVIL MUNICIPAL DE PRUEBA"
PDF_MINIMO = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


# --- diccionarios con la forma de la API ----------------------------------------------------


def dict_proceso(
    radicado: str = RADICADO,
    id_proceso: int = ID_PROCESO,
    fecha_ultima: str | None = "2024-05-10T00:00:00",
    es_privado: bool = False,
    despacho: str = DESPACHO + " ",
) -> dict:
    return {
        "idProceso": id_proceso,
        "idConexion": 180,
        "llaveProceso": radicado,
        "fechaProceso": "2024-01-15T00:00:00",
        "fechaUltimaActuacion": fecha_ultima,
        "despacho": despacho,
        "departamento": "BOGOTÁ",
        "sujetosProcesales": "Demandante: PERSONA DE PRUEBA | Demandado: ENTIDAD DE PRUEBA ",
        "esPrivado": es_privado,
        "cantFilas": -1,
    }


def dict_busqueda(procesos: list[dict] | None = None, numero: str = RADICADO) -> dict:
    procesos = procesos or []
    return {
        "tipoConsulta": "NumeroRadicacion",
        "procesos": procesos,
        "parametros": {
            "numero": numero,
            "nombre": None,
            "tipoPersona": None,
            "idSujeto": None,
            "ponente": None,
            "claseProceso": None,
            "codificacionDespacho": None,
            "soloActivos": False,
        },
        "paginacion": {
            "cantidadRegistros": len(procesos),
            "registrosPagina": 20,
            "cantidadPaginas": 1 if procesos else 0,
            "pagina": 1,
            "paginas": None,
        },
    }


def dict_actuacion(
    id_registro: int,
    consecutivo: int,
    fecha: str = "2024-05-10T00:00:00",
    actuacion: str = "Constancia secretarial",
    anotacion: str = "",
    radicado: str = RADICADO,
    con_documentos: bool = False,
    fecha_registro: str | None = None,
) -> dict:
    return {
        "idRegActuacion": id_registro,
        "llaveProceso": radicado,
        "consActuacion": consecutivo,
        "fechaActuacion": fecha,
        "actuacion": actuacion,
        "anotacion": anotacion,
        "fechaInicial": None,
        "fechaFinal": None,
        "fechaRegistro": fecha_registro or fecha,
        "codRegla": "00                              ",
        "conDocumentos": con_documentos,
        "cant": consecutivo,
    }


def dict_pagina_actuaciones(
    actuaciones: list[dict],
    pagina: int = 1,
    total_paginas: int = 1,
    total: int | None = None,
    por_pagina: int = 40,
) -> dict:
    return {
        "actuaciones": actuaciones,
        "paginacion": {
            "cantidadRegistros": total if total is not None else len(actuaciones),
            "registrosPagina": por_pagina,
            "cantidadPaginas": total_paginas,
            "pagina": pagina,
            "paginas": None,
        },
    }


def dict_detalle(radicado: str = RADICADO, ponente: str = "PONENTE DE PRUEBA") -> dict:
    return {
        "idRegProceso": 7654321,
        "llaveProceso": radicado,
        "idConexion": 180,
        "esPrivado": False,
        "fechaProceso": "2024-01-15T00:00:00",
        "codDespachoCompleto": "110014003001",
        "despacho": DESPACHO + " ",
        "ponente": ponente,
        "tipoProceso": "Ejecutivo",
        "claseProceso": "Ejecutivo Singular",
        "subclaseProceso": "Sin Subclase de Proceso",
        "recurso": "Sin Tipo de Recurso",
        "ubicacion": "Secretaria - Letra",
        "contenidoRadicacion": None,
        "fechaConsulta": "2026-09-02T01:30:35.487",
        "ultimaActualizacion": "2026-09-01T19:20:40.777",
    }


def dict_documento(id_documento: int, nombre: str = "Auto.pdf", fecha: str | None = "2026-09-01T00:00:00") -> dict:
    return {"idRegDocumento": id_documento, "nombre": nombre, "fechaPublicacion": fecha}


# --- modelos del dominio --------------------------------------------------------------------


def hacer_actuacion(
    id_registro: int,
    consecutivo: int,
    actuacion: str = "Constancia secretarial",
    anotacion: str = "",
    fecha: date = date(2024, 5, 10),
    radicado: str = RADICADO,
    id_proceso: int = ID_PROCESO,
    con_documentos: bool = False,
) -> Actuacion:
    return Actuacion(
        id_registro=id_registro,
        radicado=radicado,
        consecutivo=consecutivo,
        actuacion=actuacion,
        anotacion=anotacion,
        fecha_actuacion=fecha,
        fecha_registro=fecha,
        con_documentos=con_documentos,
        id_proceso=id_proceso,
    )


def hacer_proceso(
    id_proceso: int = ID_PROCESO,
    radicado: str = RADICADO,
    fecha_ultima: date | None = date(2024, 5, 10),
    es_privado: bool = False,
    despacho: str = DESPACHO,
) -> Proceso:
    return Proceso(
        id_proceso=id_proceso,
        radicado=radicado,
        despacho=despacho,
        departamento="BOGOTÁ",
        sujetos="Demandante: PERSONA DE PRUEBA | Demandado: ENTIDAD DE PRUEBA",
        fecha_proceso=date(2024, 1, 15),
        fecha_ultima_actuacion=fecha_ultima,
        es_privado=es_privado,
    )


def hacer_documento(id_documento: int, id_registro: int, nombre: str = "Auto.pdf", fecha: date | None = date(2026, 9, 1)) -> Documento:
    return Documento(id_documento=id_documento, id_registro=id_registro, nombre=nombre, fecha=fecha, tipo="pdf")


# --- dobles de prueba -----------------------------------------------------------------------


class RelojFalso:
    """Reloj monótono controlable. `dormir` avanza el reloj en vez de esperar."""

    def __init__(self, inicio: float = 1_000.0) -> None:
        self.ahora = inicio
        self.esperas: list[float] = []

    def __call__(self) -> float:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += segundos

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


class RelojCalendario:
    """Reloj de fecha y hora controlable para el servicio y el planificador."""

    def __init__(self, inicio: datetime) -> None:
        self.ahora = inicio
        self.esperas: list[float] = []

    def __call__(self) -> datetime:
        return self.ahora

    def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.ahora += timedelta(seconds=segundos)

    def avanzar(self, **kwargs) -> None:
        self.ahora += timedelta(**kwargs)


class FuenteFalsa:
    """Implementa el puerto `FuenteProcesos` en memoria; registra llamadas y permite programar fallos."""

    nombre = "FALSA"

    def __init__(self, por_pagina: int = 40) -> None:
        self.procesos: dict[str, list[Proceso]] = {}
        self.actuaciones: dict[int, list[Actuacion]] = {}
        self.detalles: dict[int, DetalleProceso] = {}
        self.documentos: dict[int, list[Documento]] = {}
        self.contenidos: dict[int, bytes] = {}
        self.solicitudes_realizadas = 0
        self.llamadas: list[tuple] = []
        self.error_busqueda: Exception | None = None
        self.error_actuaciones: Exception | None = None
        self.error_documentos: Exception | None = None
        self.por_pagina = por_pagina

    def registrar(self, proceso: Proceso, actuaciones: list[Actuacion] | tuple[Actuacion, ...] = ()) -> None:
        self.procesos.setdefault(proceso.radicado, []).append(proceso)
        self.actuaciones[proceso.id_proceso] = list(actuaciones)

    def registrar_documento(self, documento: Documento, contenido: bytes = PDF_MINIMO) -> None:
        self.documentos.setdefault(documento.id_registro, []).append(documento)
        self.contenidos[documento.id_documento] = contenido

    def agregar_actuacion(self, id_proceso: int, actuacion: Actuacion, nueva_fecha_ultima: date | None = None) -> None:
        self.actuaciones.setdefault(id_proceso, []).append(actuacion)
        if nueva_fecha_ultima is not None:
            for radicado, lista in self.procesos.items():
                self.procesos[radicado] = [
                    dataclasses.replace(p, fecha_ultima_actuacion=nueva_fecha_ultima) if p.id_proceso == id_proceso else p
                    for p in lista
                ]

    def buscar_por_radicado(self, radicado: str) -> list[Proceso]:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("buscar", radicado))
        if self.error_busqueda is not None:
            raise self.error_busqueda
        return list(self.procesos.get(radicado, []))

    def obtener_detalle(self, id_proceso: int) -> DetalleProceso:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("detalle", id_proceso))
        return self.detalles.get(id_proceso) or DetalleProceso(
            id_proceso=id_proceso, radicado=RADICADO, despacho=DESPACHO, ponente="PONENTE DE PRUEBA"
        )

    def obtener_actuaciones(self, id_proceso: int, pagina: int = 1) -> PaginaActuaciones:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("actuaciones", id_proceso, pagina))
        if self.error_actuaciones is not None:
            raise self.error_actuaciones
        todas = sorted(self.actuaciones.get(id_proceso, []), key=lambda a: a.consecutivo, reverse=True)
        total_paginas = max(1, math.ceil(len(todas) / self.por_pagina))
        inicio = (pagina - 1) * self.por_pagina
        return PaginaActuaciones(
            actuaciones=tuple(todas[inicio : inicio + self.por_pagina]),
            pagina=pagina,
            total_paginas=total_paginas,
            total_registros=len(todas),
            registros_por_pagina=self.por_pagina,
        )

    def listar_documentos(self, id_registro: int) -> list[Documento]:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("documentos", id_registro))
        if self.error_documentos is not None:
            raise self.error_documentos
        return list(self.documentos.get(id_registro, []))

    def descargar_documento(self, id_documento: int) -> DescargaDocumento:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("descarga", id_documento))
        if id_documento not in self.contenidos:
            raise ErrorFuente(f"HTTP 404: el documento {id_documento} no existe", codigo=404)
        nombre = next((d.nombre for docs in self.documentos.values() for d in docs if d.id_documento == id_documento), "")
        return DescargaDocumento(
            id_documento=id_documento, contenido=self.contenidos[id_documento], nombre=nombre, tipo_contenido="application/pdf"
        )

    def cerrar(self) -> None:
        return None


class NotificadorRegistro:
    def __init__(self) -> None:
        self.eventos = []

    def notificar(self, evento) -> None:
        self.eventos.append(evento)


# --- publicaciones procesales -------------------------------------------------------------------

from consultor_procesos.dominio.modelos import DocumentoPublicado, PaginaPublicaciones, Publicacion  # noqa: E402

DESPACHO_CODIGO = RADICADO[:12]
URL_PUB = "https://publicacionesprocesales.ramajudicial.gov.co"


def hacer_publicacion(
    id_publicacion: str = "256567231",
    titulo: str = "Notificación por Estado No.82 de 31 de agosto de 2026",
    tipo: str = "Notificaciones por Estados",
    id_estructura: int = 6098957,
    despacho_codigo: str = DESPACHO_CODIGO,
    fecha: date | None = date(2026, 8, 31),
    resumen: str = "ESTADO VER AUTOS VER",
    documentos: tuple[DocumentoPublicado, ...] = (
        DocumentoPublicado("ESTADO", URL_PUB + "/documents/6098902/1/estado+82.pdf/aaaa"),
        DocumentoPublicado("AUTOS", URL_PUB + "/documents/6098902/1/AUTOS+ESTADO+82.pdf/bbbb"),
    ),
) -> Publicacion:
    return Publicacion(
        id_publicacion=id_publicacion,
        tipo=tipo,
        despacho_codigo=despacho_codigo,
        despacho=DESPACHO,
        titulo=titulo,
        url_detalle=f"{URL_PUB}/web/publicaciones-procesales/inicio?articleId={id_publicacion}",
        id_estructura=id_estructura,
        fecha_publicacion=fecha,
        resumen=resumen,
        documentos=documentos,
        departamento="BOGOTÁ",
        municipio="BOGOTÁ D.C.",
        entidad="JUZGADO MUNICIPAL",
        especialidad="CIVIL",
    )


def pdf_con_texto(texto: str) -> bytes:
    """Construye un PDF mínimo pero válido (con tabla xref) que contiene `texto` en Helvetica."""
    texto_pdf = texto.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    contenido = f"BT /F1 11 Tf 40 750 Td ({texto_pdf}) Tj ET".encode("latin-1", "replace")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(contenido)).encode() + b" >>\nstream\n" + contenido + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = bytearray(b"%PDF-1.4\n")
    desplazamientos = []
    for indice, cuerpo in enumerate(objetos, start=1):
        desplazamientos.append(len(salida))
        salida += f"{indice} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n".encode() + b"0000000000 65535 f \n"
    for d in desplazamientos:
        salida += f"{d:010d} 00000 n \n".encode()
    salida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n".encode()
    return bytes(salida)


class FuentePublicacionesFalsa:
    """Implementa `FuentePublicaciones` en memoria: publicaciones por (despacho, tipo) y contenidos por URL."""

    nombre = "PUBLICACIONES FALSAS"

    def __init__(self) -> None:
        self.publicaciones: dict[tuple[str, int], list[Publicacion]] = {}
        self.contenidos: dict[str, bytes] = {}
        self.solicitudes_realizadas = 0
        self.llamadas: list[tuple] = []
        self.error_listar: Exception | None = None
        self.error_descargar: Exception | None = None

    def registrar(self, publicacion: Publicacion, contenidos: dict[str, bytes] | None = None) -> None:
        clave = (publicacion.despacho_codigo, publicacion.id_estructura or 0)
        self.publicaciones.setdefault(clave, []).append(publicacion)
        for url, datos in (contenidos or {}).items():
            self.contenidos[url] = datos

    def listar_publicaciones(self, despacho_codigo, id_estructura, desde, hasta, pagina=1, por_pagina=75) -> PaginaPublicaciones:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("listar", despacho_codigo, id_estructura, desde, hasta, pagina))
        if self.error_listar is not None:
            raise self.error_listar
        todas = [
            p
            for p in self.publicaciones.get((despacho_codigo, id_estructura), [])
            if p.fecha_publicacion is None or desde <= p.fecha_publicacion <= hasta
        ]
        inicio = (pagina - 1) * por_pagina
        return PaginaPublicaciones(tuple(todas[inicio : inicio + por_pagina]), pagina=pagina, por_pagina=por_pagina, total=len(todas))

    def descargar(self, url: str) -> bytes:
        self.solicitudes_realizadas += 1
        self.llamadas.append(("descargar", url))
        if self.error_descargar is not None:
            raise self.error_descargar
        if url not in self.contenidos:
            raise ErrorFuente(f"HTTP 404: no existe {url}", codigo=404)
        return self.contenidos[url]

    def cerrar(self) -> None:
        return None
