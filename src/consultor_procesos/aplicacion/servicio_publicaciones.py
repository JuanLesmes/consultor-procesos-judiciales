"""Revisión de las publicaciones procesales (estados, avisos, traslados) de los despachos vigilados.

Por cada despacho que aparezca en los radicados vigilados se consultan, como mucho una vez
cada `horas_entre_revisiones`, los tipos de publicación configurados dentro de una ventana
de fechas. Las publicaciones nuevas se guardan y se buscan en ellas los radicados vigilados:
primero en el título y el resumen; si el listado no trae documentos, en la página de detalle
de la publicación (muchos despachos publican allí cada auto como un PDF con el radicado corto
en el nombre, "2025-00451 ...pdf"); y por último dentro de los PDF (la planilla del estado, los
autos). Cada coincidencia se notifica como una novedad del radicado con el enlace directo a la
publicación y al documento donde aparece.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..dominio.errores import ErrorConsultor, ErrorFuente, FuenteNoDisponible, PresupuestoAgotado, RespuestaInesperada
from ..dominio.modelos import (
    CoincidenciaPublicacion,
    DocumentoPublicado,
    EstadoVerificacion,
    EventoNovedades,
    ProcesoVigilado,
    Publicacion,
    ResultadoPublicaciones,
)
from ..dominio.puertos import FuentePublicaciones, Notificador, Repositorio
from ..dominio.reglas import buscar_radicado
from ..enlaces import TIPOS_PUBLICACION
from ..infraestructura.pdf import extraer_texto_pdf

log = logging.getLogger(__name__)

# Documentos que listan varios procesos a la vez: se leen primero si hay que abrir PDF.
PALABRAS_DOCUMENTO_GENERAL = ("PLANILLA", "ESTADO", "TRASLADO", "AVISO", "EDICTO", "LISTADO", "RELACION")
MAX_DOCUMENTOS_GUARDADOS = 10
_RE_INICIA_CON_RADICADO = re.compile(r"\s*(\d{4}\s*-\s*\d{3,6}|\d{23}|\d{21})")

TIPOS_PREDETERMINADOS: tuple[int, ...] = (
    TIPOS_PUBLICACION["Notificaciones por Estados"],
    TIPOS_PUBLICACION["Notificaciones por Aviso"],
    TIPOS_PUBLICACION["Traslados especiales y ordinarios"],
    TIPOS_PUBLICACION["Autos masivo"],
)


@dataclass
class OpcionesPublicaciones:
    tipos: tuple[int, ...] = TIPOS_PREDETERMINADOS
    horas_entre_revisiones: float = 20.0
    dias_ventana_inicial: int = 7
    dias_solapamiento: int = 1
    analizar_pdf: bool = True
    analizar_detalle: bool = True  # abrir la página de detalle cuando el listado no trae documentos
    max_documentos_por_publicacion: int = 3
    max_paginas: int = 3
    por_pagina: int = 75
    pausa_entre_despachos_segundos: float = 5.0
    max_bytes_pdf: int = 15_000_000


def _es_documento_general(documento: DocumentoPublicado) -> bool:
    """La planilla del estado, el listado de traslados... Un archivo que empieza por un radicado
    corto ("2022-00850 Auto Corre Traslado.pdf") es el auto de un proceso, aunque diga "traslado"."""
    etiqueta = documento.etiqueta.upper()
    if _RE_INICIA_CON_RADICADO.match(etiqueta):
        return False
    return any(palabra in etiqueta for palabra in PALABRAS_DOCUMENTO_GENERAL)


class ServicioPublicaciones:
    def __init__(
        self,
        fuente: FuentePublicaciones,
        repositorio: Repositorio,
        notificador: Notificador,
        opciones: OpcionesPublicaciones | None = None,
        extractor_texto: Callable[[bytes], str] = extraer_texto_pdf,
        ahora: Callable[[], datetime] = datetime.now,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fuente = fuente
        self._repositorio = repositorio
        self._notificador = notificador
        self._opciones = opciones or OpcionesPublicaciones()
        self._extractor = extractor_texto
        self._ahora = ahora
        self._dormir = dormir

    @property
    def fuente(self) -> FuentePublicaciones:
        return self._fuente

    @property
    def opciones(self) -> OpcionesPublicaciones:
        return self._opciones

    # --- consultas locales -----------------------------------------------------------------

    def coincidencias_recientes(
        self, radicado: str | None = None, limite: int = 50, solo_pendientes: bool = False
    ) -> list[CoincidenciaPublicacion]:
        return self._repositorio.listar_coincidencias(radicado=radicado, limite=limite, solo_pendientes=solo_pendientes)

    def contar_pendientes(self) -> int:
        return self._repositorio.contar_coincidencias_pendientes()

    def marcar_revisada(self, id_coincidencia: int, revisada: bool = True) -> bool:
        return self._repositorio.marcar_coincidencia_revisada(id_coincidencia, revisada)

    def publicaciones_despacho(self, despacho_codigo: str, limite: int = 50) -> list[Publicacion]:
        return self._repositorio.listar_publicaciones(despacho_codigo=despacho_codigo, limite=limite)

    # --- revisión -----------------------------------------------------------------------------

    @staticmethod
    def despachos_de(vigilados: Iterable[ProcesoVigilado]) -> dict[str, list[ProcesoVigilado]]:
        """Agrupa los radicados por cada código de despacho (12 dígitos) que hay que revisar."""
        por_despacho: dict[str, list[ProcesoVigilado]] = {}
        for vigilado in vigilados:
            for codigo in vigilado.codigos_despacho:
                por_despacho.setdefault(codigo, []).append(vigilado)
        return dict(sorted(por_despacho.items()))

    def revisar(self, vigilados: Iterable[ProcesoVigilado], forzar: bool = False) -> list[ResultadoPublicaciones]:
        """Revisa los despachos de los radicados dados. Si la fuente cae, el resto del lote se pospone."""
        resultados: list[ResultadoPublicaciones] = []
        motivo_detencion: str | None = None
        primero = True
        for codigo, lista in self.despachos_de(vigilados).items():
            if motivo_detencion is not None:
                resultados.append(
                    ResultadoPublicaciones(codigo, EstadoVerificacion.OMITIDO, self._ahora(), mensaje=motivo_detencion)
                )
                continue
            if not primero and self._opciones.pausa_entre_despachos_segundos > 0:
                self._dormir(self._opciones.pausa_entre_despachos_segundos)
            primero = False
            try:
                resultados.append(self.revisar_despacho(codigo, lista, forzar=forzar))
            except (FuenteNoDisponible, PresupuestoAgotado) as exc:
                motivo_detencion = f"Lote de publicaciones detenido: {exc}"
                log.error(motivo_detencion)
                resultados.append(ResultadoPublicaciones(codigo, EstadoVerificacion.OMITIDO, self._ahora(), mensaje=str(exc)))
        return resultados

    def revisar_despacho(
        self, despacho_codigo: str, vigilados: list[ProcesoVigilado], forzar: bool = False
    ) -> ResultadoPublicaciones:
        momento = self._ahora()
        ultima = self._repositorio.obtener_revision_despacho(despacho_codigo)
        if not forzar and ultima is not None:
            transcurrido = momento - ultima
            if transcurrido < timedelta(hours=self._opciones.horas_entre_revisiones):
                horas = transcurrido.total_seconds() / 3600
                return ResultadoPublicaciones(
                    despacho_codigo,
                    EstadoVerificacion.SIN_CAMBIOS,
                    momento,
                    mensaje=f"Revisado hace {horas:.1f} h; se vuelve a revisar cada {self._opciones.horas_entre_revisiones:g} h.",
                )

        if ultima is not None:
            desde = ultima.date() - timedelta(days=self._opciones.dias_solapamiento)
        else:
            desde = momento.date() - timedelta(days=self._opciones.dias_ventana_inicial)
        hasta = momento.date()
        solicitudes_antes = self._fuente.solicitudes_realizadas
        conocidos = self._repositorio.ids_publicaciones_conocidas(despacho_codigo)
        radicados = [v.radicado for v in vigilados]

        try:
            nuevas, nombre_despacho = self._descargar_nuevas(despacho_codigo, desde, hasta, conocidos)
            analizadas: list[Publicacion] = []
            coincidencias: list[CoincidenciaPublicacion] = []
            for publicacion in nuevas:
                publicacion_analizada, encontradas = self.analizar_publicacion(publicacion, radicados)
                analizadas.append(publicacion_analizada)
                coincidencias.extend(encontradas)
            self._repositorio.guardar_publicaciones(analizadas, momento)
            self._repositorio.guardar_coincidencias(coincidencias, momento)
            self._repositorio.registrar_revision_despacho(despacho_codigo, momento)
        except (FuenteNoDisponible, PresupuestoAgotado):
            raise
        except ErrorConsultor as exc:
            log.warning("Error revisando las publicaciones del despacho %s: %s", despacho_codigo, exc)
            return ResultadoPublicaciones(
                despacho_codigo,
                EstadoVerificacion.ERROR,
                momento,
                mensaje=str(exc),
                solicitudes=self._fuente.solicitudes_realizadas - solicitudes_antes,
            )

        self._notificar(coincidencias, vigilados, momento)
        mensaje = (
            f"{len(nuevas)} publicación(es) nueva(s) entre {desde.isoformat()} y {hasta.isoformat()}; "
            f"{len(coincidencias)} mencionan radicados vigilados."
        )
        return ResultadoPublicaciones(
            despacho_codigo,
            EstadoVerificacion.OK,
            momento,
            despacho=nombre_despacho,
            nuevas=len(nuevas),
            coincidencias=coincidencias,
            mensaje=mensaje,
            solicitudes=self._fuente.solicitudes_realizadas - solicitudes_antes,
        )

    def _descargar_nuevas(self, despacho_codigo: str, desde, hasta, conocidos: set[str]) -> tuple[list[Publicacion], str]:
        nuevas: list[Publicacion] = []
        vistas: set[str] = set(conocidos)
        nombre_despacho = ""
        for id_estructura in self._opciones.tipos:
            pagina = 1
            while pagina <= max(1, self._opciones.max_paginas):
                resultado = self._fuente.listar_publicaciones(
                    despacho_codigo, id_estructura, desde, hasta, pagina=pagina, por_pagina=self._opciones.por_pagina
                )
                for publicacion in resultado.publicaciones:
                    if publicacion.despacho_codigo and publicacion.despacho_codigo != despacho_codigo:
                        continue  # el portal a veces ignora el filtro y devuelve otros despachos
                    nombre_despacho = nombre_despacho or publicacion.despacho
                    if publicacion.id_publicacion in vistas:
                        continue
                    vistas.add(publicacion.id_publicacion)
                    nuevas.append(publicacion if publicacion.despacho_codigo else replace(publicacion, despacho_codigo=despacho_codigo))
                if not resultado.hay_mas or not resultado.publicaciones:
                    break
                pagina += 1
        return nuevas, nombre_despacho

    def analizar_publicacion(
        self, publicacion: Publicacion, radicados: Iterable[str]
    ) -> tuple[Publicacion, list[CoincidenciaPublicacion]]:
        """Busca los radicados en el título y el resumen, luego en el detalle y, si hace falta, en los PDF."""
        pendientes = list(dict.fromkeys(radicados))
        hallazgos: list[tuple[str, str, str, str]] = []  # (radicado, forma, dónde, fragmento)

        def buscar_en(texto: str, donde: str) -> None:
            for radicado in list(pendientes):
                hallazgo = buscar_radicado(texto, radicado)
                if hallazgo:
                    hallazgos.append((radicado, hallazgo[0], donde, hallazgo[1]))
                    pendientes.remove(radicado)

        buscar_en(f"{publicacion.titulo}\n{publicacion.resumen}", "titulo o resumen")
        analizada = not publicacion.documentos

        if pendientes and self._opciones.analizar_detalle and publicacion.url_detalle and not publicacion.documentos:
            try:
                detalle = self._fuente.obtener_detalle(publicacion.url_detalle)
            except RespuestaInesperada:
                raise
            except ErrorFuente as exc:
                log.warning("No se pudo abrir el detalle de la publicación %s: %s", publicacion.id_publicacion, exc)
            else:
                analizada = True
                encontrados: list[DocumentoPublicado] = []
                for documento in detalle.documentos:
                    for radicado in list(pendientes):
                        hallazgo = buscar_radicado(documento.etiqueta, radicado)
                        if hallazgo:
                            # El fragmento es el nombre del archivo tal cual: es lo que la persona va a buscar.
                            hallazgos.append((radicado, hallazgo[0], f"documento: {documento.etiqueta}", documento.etiqueta))
                            pendientes.remove(radicado)
                            if documento not in encontrados:
                                encontrados.append(documento)
                buscar_en(detalle.texto, "detalle de la publicación")
                generales = [d for d in detalle.documentos if d not in encontrados and _es_documento_general(d)]
                otros = [d for d in detalle.documentos if d not in encontrados and d not in generales]
                # Se guardan los documentos donde apareció algún radicado y los que listan varios procesos.
                guardados = (encontrados + generales)[:MAX_DOCUMENTOS_GUARDADOS]
                publicacion = replace(publicacion, documentos=tuple(guardados))
                candidatos = generales + otros
                if pendientes and self._opciones.analizar_pdf:
                    self._buscar_en_pdf(candidatos, pendientes, hallazgos)
        elif pendientes and self._opciones.analizar_pdf and publicacion.documentos:
            analizada = self._buscar_en_pdf(list(publicacion.documentos), pendientes, hallazgos) or analizada

        publicacion = replace(publicacion, analizada=analizada)
        coincidencias = [
            CoincidenciaPublicacion(publicacion, radicado, forma, donde=donde, fragmento=fragmento)
            for radicado, forma, donde, fragmento in hallazgos
        ]
        return publicacion, coincidencias

    def _buscar_en_pdf(
        self, documentos: list[DocumentoPublicado], pendientes: list[str], hallazgos: list[tuple[str, str, str, str]]
    ) -> bool:
        """Descarga hasta `max_documentos_por_publicacion` PDF y busca los radicados pendientes. True si leyó alguno."""
        leido = False
        for documento in documentos[: self._opciones.max_documentos_por_publicacion]:
            try:
                datos = self._fuente.descargar(documento.url)
            except ErrorFuente as exc:
                log.warning("No se pudo descargar %s: %s", documento.url, exc)
                continue
            if len(datos) > self._opciones.max_bytes_pdf:
                log.warning("Documento demasiado grande (%d bytes), se omite: %s", len(datos), documento.url)
                continue
            texto = self._extractor(datos)
            if not texto.strip():
                continue
            leido = True
            for radicado in list(pendientes):
                hallazgo = buscar_radicado(texto, radicado)
                if hallazgo:
                    hallazgos.append((radicado, hallazgo[0], f"documento: {documento.etiqueta}", hallazgo[1]))
                    pendientes.remove(radicado)
            if not pendientes:
                break
        return leido

    def _notificar(
        self, coincidencias: list[CoincidenciaPublicacion], vigilados: list[ProcesoVigilado], momento: datetime
    ) -> None:
        por_radicado: dict[str, list[CoincidenciaPublicacion]] = {}
        for coincidencia in coincidencias:
            por_radicado.setdefault(coincidencia.radicado, []).append(coincidencia)
        alias = {v.radicado: v.alias for v in vigilados}
        for radicado, lista in por_radicado.items():
            self._notificador.notificar(
                EventoNovedades(
                    radicado=radicado,
                    alias=alias.get(radicado),
                    procesos=[],
                    novedades=[],
                    momento=momento,
                    publicaciones=lista,
                )
            )
