"""Repositorio en memoria. Implementa el mismo puerto que SQLite; sirve de referencia y para pruebas."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from datetime import date, datetime

from ...dominio.modelos import (
    CoincidenciaPublicacion,
    Documento,
    Novedad,
    ProcesoVigilado,
    Publicacion,
    ResultadoVerificacion,
)


class RepositorioMemoria:
    def __init__(self) -> None:
        self._vigilados: dict[str, ProcesoVigilado] = {}
        self._actuaciones: dict[str, dict[int, Novedad]] = {}
        self._documentos: dict[int, dict[int, Documento]] = {}
        self._documentos_consultados: set[int] = set()
        self._rutas_locales: dict[int, str] = {}
        self._publicaciones: dict[str, Publicacion] = {}
        self._coincidencias: list[CoincidenciaPublicacion] = []
        self._revisiones: dict[str, datetime] = {}
        self._verificaciones: list[dict] = []
        self._contadores: dict[date, int] = {}

    # --- vigilados ---
    def guardar_vigilado(self, vigilado: ProcesoVigilado) -> None:
        self._vigilados[vigilado.radicado] = dataclasses.replace(vigilado)

    def obtener_vigilado(self, radicado: str) -> ProcesoVigilado | None:
        vigilado = self._vigilados.get(radicado)
        return dataclasses.replace(vigilado) if vigilado else None

    def listar_vigilados(self, solo_activos: bool = True) -> list[ProcesoVigilado]:
        return [
            dataclasses.replace(v)
            for v in sorted(self._vigilados.values(), key=lambda v: v.radicado)
            if v.activo or not solo_activos
        ]

    def eliminar_vigilado(self, radicado: str) -> bool:
        existia = radicado in self._vigilados
        self._vigilados.pop(radicado, None)
        for id_registro in list(self._actuaciones.pop(radicado, {})):
            self._documentos.pop(id_registro, None)
            self._documentos_consultados.discard(id_registro)
        self._coincidencias = [c for c in self._coincidencias if c.radicado != radicado]
        return existia

    # --- actuaciones y novedades ---
    def _todas(self) -> list[Novedad]:
        return [n for por_radicado in self._actuaciones.values() for n in por_radicado.values()]

    def _con_documentos(self, novedad: Novedad) -> Novedad:
        documentos = tuple(sorted(self._documentos.get(novedad.actuacion.id_registro, {}).values(), key=lambda d: d.id_documento))
        return dataclasses.replace(novedad, documentos=documentos)

    def ids_actuaciones_conocidas(self, radicado: str) -> set[int]:
        return set(self._actuaciones.get(radicado, {}))

    def guardar_novedades(self, novedades: Iterable[Novedad], momento: datetime) -> int:
        insertadas = 0
        for novedad in novedades:
            por_radicado = self._actuaciones.setdefault(novedad.radicado, {})
            id_registro = novedad.actuacion.id_registro
            if id_registro in por_radicado:
                continue
            por_radicado[id_registro] = dataclasses.replace(novedad, visto_en=momento, documentos=())
            insertadas += 1
            if novedad.documentos:
                self.guardar_documentos(id_registro, novedad.documentos, momento)
        return insertadas

    def listar_actuaciones(self, radicado: str, solo_autos: bool = False) -> list[Novedad]:
        novedades = self._actuaciones.get(radicado, {}).values()
        return [
            self._con_documentos(n)
            for n in sorted(
                (n for n in novedades if n.es_auto or not solo_autos),
                key=lambda n: (n.actuacion.consecutivo, n.actuacion.id_registro),
            )
        ]

    def obtener_novedad(self, id_registro: int) -> Novedad | None:
        for por_radicado in self._actuaciones.values():
            if id_registro in por_radicado:
                return self._con_documentos(por_radicado[id_registro])
        return None

    def listar_novedades_recientes(
        self,
        limite: int = 50,
        solo_autos: bool = False,
        radicado: str | None = None,
        solo_pendientes: bool = False,
    ) -> list[Novedad]:
        candidatas = [
            n
            for n in self._todas()
            if (not solo_autos or n.es_auto)
            and (radicado is None or n.radicado == radicado)
            and (not solo_pendientes or not n.revisada)
        ]
        candidatas.sort(
            key=lambda n: (
                n.visto_en or datetime.min,
                n.actuacion.fecha_actuacion or date.min,
                n.actuacion.consecutivo,
                n.actuacion.id_registro,
            ),
            reverse=True,
        )
        return [self._con_documentos(n) for n in candidatas[: max(1, limite)]]

    def contar_pendientes(self, solo_autos: bool = False) -> int:
        return sum(1 for n in self._todas() if not n.revisada and (n.es_auto or not solo_autos))

    def marcar_revisada(self, id_registro: int, revisada: bool = True) -> bool:
        for por_radicado in self._actuaciones.values():
            if id_registro in por_radicado:
                por_radicado[id_registro] = dataclasses.replace(por_radicado[id_registro], revisada=revisada)
                return True
        return False

    # --- documentos ---
    def guardar_documentos(self, id_registro: int, documentos: Iterable[Documento], momento: datetime) -> None:
        almacen = self._documentos.setdefault(id_registro, {})
        for documento in documentos:
            almacen[documento.id_documento] = dataclasses.replace(documento, id_registro=id_registro)
        self._documentos_consultados.add(id_registro)

    def listar_documentos(self, id_registro: int) -> list[Documento] | None:
        documentos = self._documentos.get(id_registro)
        if documentos:
            return sorted(documentos.values(), key=lambda d: d.id_documento)
        return [] if id_registro in self._documentos_consultados else None

    def obtener_documento(self, id_documento: int) -> Documento | None:
        for documentos in self._documentos.values():
            if id_documento in documentos:
                return documentos[id_documento]
        return None

    def registrar_descarga(self, id_documento: int, ruta_local: str, momento: datetime) -> None:
        self._rutas_locales[id_documento] = ruta_local

    def ruta_local_documento(self, id_documento: int) -> str | None:
        return self._rutas_locales.get(id_documento)

    # --- publicaciones procesales ---
    def ids_publicaciones_conocidas(self, despacho_codigo: str) -> set[str]:
        return {p.id_publicacion for p in self._publicaciones.values() if p.despacho_codigo == despacho_codigo}

    def guardar_publicaciones(self, publicaciones: Iterable[Publicacion], momento: datetime) -> int:
        insertadas = 0
        for publicacion in publicaciones:
            existente = self._publicaciones.get(publicacion.id_publicacion)
            if existente is None:
                self._publicaciones[publicacion.id_publicacion] = dataclasses.replace(
                    publicacion, visto_en=publicacion.visto_en or momento
                )
                insertadas += 1
            else:
                self._publicaciones[publicacion.id_publicacion] = dataclasses.replace(
                    existente,
                    analizada=existente.analizada or publicacion.analizada,
                    resumen=publicacion.resumen or existente.resumen,
                    documentos=publicacion.documentos or existente.documentos,
                )
        return insertadas

    def listar_publicaciones(self, despacho_codigo: str | None = None, limite: int = 50) -> list[Publicacion]:
        candidatas = [p for p in self._publicaciones.values() if despacho_codigo is None or p.despacho_codigo == despacho_codigo]
        candidatas.sort(key=lambda p: (p.fecha_publicacion or date.min, p.visto_en or datetime.min), reverse=True)
        return candidatas[: max(1, limite)]

    def obtener_publicacion(self, id_publicacion: str) -> Publicacion | None:
        return self._publicaciones.get(id_publicacion)

    def guardar_coincidencias(self, coincidencias: Iterable[CoincidenciaPublicacion], momento: datetime) -> int:
        insertadas = 0
        for coincidencia in coincidencias:
            clave = (coincidencia.publicacion.id_publicacion, coincidencia.radicado, coincidencia.donde)
            if any((c.publicacion.id_publicacion, c.radicado, c.donde) == clave for c in self._coincidencias):
                continue
            siguiente = max((c.id or 0 for c in self._coincidencias), default=0) + 1
            self._coincidencias.append(
                dataclasses.replace(coincidencia, id=siguiente, visto_en=coincidencia.visto_en or momento)
            )
            insertadas += 1
        return insertadas

    def _coincidencia_actual(self, coincidencia: CoincidenciaPublicacion) -> CoincidenciaPublicacion:
        publicacion = self._publicaciones.get(coincidencia.publicacion.id_publicacion, coincidencia.publicacion)
        return dataclasses.replace(coincidencia, publicacion=publicacion)

    def listar_coincidencias(
        self, radicado: str | None = None, limite: int = 50, solo_pendientes: bool = False
    ) -> list[CoincidenciaPublicacion]:
        candidatas = [
            c
            for c in self._coincidencias
            if (radicado is None or c.radicado == radicado) and (not solo_pendientes or not c.revisada)
        ]
        candidatas.sort(key=lambda c: (c.visto_en or datetime.min, c.id or 0), reverse=True)
        return [self._coincidencia_actual(c) for c in candidatas[: max(1, limite)]]

    def contar_coincidencias_pendientes(self) -> int:
        return sum(1 for c in self._coincidencias if not c.revisada)

    def marcar_coincidencia_revisada(self, id_coincidencia: int, revisada: bool = True) -> bool:
        for indice, coincidencia in enumerate(self._coincidencias):
            if coincidencia.id == id_coincidencia:
                self._coincidencias[indice] = dataclasses.replace(coincidencia, revisada=revisada)
                return True
        return False

    def obtener_revision_despacho(self, despacho_codigo: str) -> datetime | None:
        return self._revisiones.get(despacho_codigo)

    def registrar_revision_despacho(self, despacho_codigo: str, momento: datetime) -> None:
        self._revisiones[despacho_codigo] = momento

    # --- verificaciones ---
    def registrar_verificacion(self, resultado: ResultadoVerificacion) -> None:
        self._verificaciones.append(
            {
                "radicado": resultado.radicado,
                "momento": resultado.momento,
                "estado": resultado.estado.value,
                "novedades": len(resultado.novedades),
                "autos": len(resultado.autos),
                "solicitudes": resultado.solicitudes,
                "mensaje": resultado.mensaje,
            }
        )

    def listar_verificaciones(self, radicado: str | None = None, limite: int = 50) -> list[dict]:
        filas = [v for v in self._verificaciones if radicado is None or v["radicado"] == radicado]
        return list(reversed(filas))[:limite]

    # --- contadores ---
    def obtener_contador(self, fecha: date) -> int:
        return self._contadores.get(fecha, 0)

    def incrementar_contador(self, fecha: date, cantidad: int = 1) -> int:
        self._contadores[fecha] = self._contadores.get(fecha, 0) + cantidad
        return self._contadores[fecha]

    def cerrar(self) -> None:
        return None
