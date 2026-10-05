"""Revisión de las publicaciones procesales (estados, avisos, traslados) de los despachos vigilados.

Por cada despacho que aparezca en los radicados vigilados se consultan, como mucho una vez
cada `horas_entre_revisiones`, los tipos de publicación configurados dentro de una ventana
de fechas. Las publicaciones nuevas se guardan y se buscan en ellas los radicados vigilados:
primero en el título y el resumen; si no aparecen y la publicación trae PDF (el estado, los
autos del estado), se descargan y se lee su texto. Cada coincidencia se notifica como una
novedad del radicado con el enlace directo a la publicación y a sus documentos.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..dominio.errores import ErrorConsultor, ErrorFuente, FuenteNoDisponible, PresupuestoAgotado
from ..dominio.modelos import (
    CoincidenciaPublicacion,
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
    max_documentos_por_publicacion: int = 3
    max_paginas: int = 3
    por_pagina: int = 75
    pausa_entre_despachos_segundos: float = 5.0
    max_bytes_pdf: int = 15_000_000


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
        """Busca los radicados en el título y el resumen y, si hace falta, dentro de los PDF enlazados."""
        pendientes = list(dict.fromkeys(radicados))
        coincidencias: list[CoincidenciaPublicacion] = []
        texto_base = f"{publicacion.titulo}\n{publicacion.resumen}"
        for radicado in list(pendientes):
            hallazgo = buscar_radicado(texto_base, radicado)
            if hallazgo:
                forma, fragmento = hallazgo
                coincidencias.append(
                    CoincidenciaPublicacion(publicacion, radicado, forma, donde="titulo o resumen", fragmento=fragmento)
                )
                pendientes.remove(radicado)

        analizada = not publicacion.documentos
        if pendientes and self._opciones.analizar_pdf and publicacion.documentos:
            for documento in publicacion.documentos[: self._opciones.max_documentos_por_publicacion]:
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
                analizada = True
                for radicado in list(pendientes):
                    hallazgo = buscar_radicado(texto, radicado)
                    if hallazgo:
                        forma, fragmento = hallazgo
                        coincidencias.append(
                            CoincidenciaPublicacion(
                                publicacion, radicado, forma, donde=f"documento: {documento.etiqueta}", fragmento=fragmento
                            )
                        )
                        pendientes.remove(radicado)
                if not pendientes:
                    break
        return replace(publicacion, analizada=analizada), coincidencias

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
