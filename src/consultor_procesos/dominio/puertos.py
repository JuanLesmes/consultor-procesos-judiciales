"""Puertos (interfaces) que la aplicación necesita y que los adaptadores implementan."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from .modelos import (
    CoincidenciaPublicacion,
    DescargaDocumento,
    DetalleProceso,
    Documento,
    EventoNovedades,
    Novedad,
    PaginaActuaciones,
    PaginaPublicaciones,
    Proceso,
    ProcesoVigilado,
    Publicacion,
    ResultadoVerificacion,
)


@runtime_checkable
class FuenteProcesos(Protocol):
    """Una fuente de datos de procesos (hoy la CPNU; a futuro SAMAI u otras)."""

    nombre: str
    solicitudes_realizadas: int

    def buscar_por_radicado(self, radicado: str) -> list[Proceso]: ...

    def obtener_detalle(self, id_proceso: int) -> DetalleProceso: ...

    def obtener_actuaciones(self, id_proceso: int, pagina: int = 1) -> PaginaActuaciones: ...

    def listar_documentos(self, id_registro: int) -> list[Documento]: ...

    def descargar_documento(self, id_documento: int) -> DescargaDocumento: ...


@runtime_checkable
class FuentePublicaciones(Protocol):
    """Una fuente de publicaciones procesales por despacho (hoy el portal Publicaciones Procesales)."""

    nombre: str
    solicitudes_realizadas: int

    def listar_publicaciones(
        self,
        despacho_codigo: str,
        id_estructura: int,
        desde: date,
        hasta: date,
        pagina: int = 1,
        por_pagina: int = 75,
    ) -> PaginaPublicaciones: ...

    def descargar(self, url: str) -> bytes: ...


@runtime_checkable
class ContadorSolicitudes(Protocol):
    def obtener_contador(self, fecha: date) -> int: ...

    def incrementar_contador(self, fecha: date, cantidad: int = 1) -> int: ...


@runtime_checkable
class Repositorio(ContadorSolicitudes, Protocol):
    # --- radicados vigilados ---
    def guardar_vigilado(self, vigilado: ProcesoVigilado) -> None: ...

    def obtener_vigilado(self, radicado: str) -> ProcesoVigilado | None: ...

    def listar_vigilados(self, solo_activos: bool = True) -> list[ProcesoVigilado]: ...

    def eliminar_vigilado(self, radicado: str) -> bool: ...

    # --- actuaciones y novedades ---
    def ids_actuaciones_conocidas(self, radicado: str) -> set[int]: ...

    def guardar_novedades(self, novedades: Iterable[Novedad], momento: datetime) -> int: ...

    def listar_actuaciones(self, radicado: str, solo_autos: bool = False) -> list[Novedad]: ...

    def obtener_novedad(self, id_registro: int) -> Novedad | None: ...

    def listar_novedades_recientes(
        self,
        limite: int = 50,
        solo_autos: bool = False,
        radicado: str | None = None,
        solo_pendientes: bool = False,
    ) -> list[Novedad]: ...

    def contar_pendientes(self, solo_autos: bool = False) -> int: ...

    def marcar_revisada(self, id_registro: int, revisada: bool = True) -> bool: ...

    # --- documentos ---
    def guardar_documentos(self, id_registro: int, documentos: Iterable[Documento], momento: datetime) -> None: ...

    def listar_documentos(self, id_registro: int) -> list[Documento] | None: ...

    def obtener_documento(self, id_documento: int) -> Documento | None: ...

    def registrar_descarga(self, id_documento: int, ruta_local: str, momento: datetime) -> None: ...

    def ruta_local_documento(self, id_documento: int) -> str | None: ...

    # --- publicaciones procesales ---
    def ids_publicaciones_conocidas(self, despacho_codigo: str) -> set[str]: ...

    def guardar_publicaciones(self, publicaciones: Iterable[Publicacion], momento: datetime) -> int: ...

    def listar_publicaciones(self, despacho_codigo: str | None = None, limite: int = 50) -> list[Publicacion]: ...

    def obtener_publicacion(self, id_publicacion: str) -> Publicacion | None: ...

    def guardar_coincidencias(self, coincidencias: Iterable[CoincidenciaPublicacion], momento: datetime) -> int: ...

    def listar_coincidencias(
        self, radicado: str | None = None, limite: int = 50, solo_pendientes: bool = False
    ) -> list[CoincidenciaPublicacion]: ...

    def contar_coincidencias_pendientes(self) -> int: ...

    def marcar_coincidencia_revisada(self, id_coincidencia: int, revisada: bool = True) -> bool: ...

    def obtener_revision_despacho(self, despacho_codigo: str) -> datetime | None: ...

    def registrar_revision_despacho(self, despacho_codigo: str, momento: datetime) -> None: ...

    # --- bitácora ---
    def registrar_verificacion(self, resultado: ResultadoVerificacion) -> None: ...

    def listar_verificaciones(self, radicado: str | None = None, limite: int = 50) -> list[dict]: ...


@runtime_checkable
class Notificador(Protocol):
    def notificar(self, evento: EventoNovedades) -> None: ...
