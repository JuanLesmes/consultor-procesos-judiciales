"""Modelos del dominio.

Los nombres siguen la terminología de la Rama Judicial (radicado, actuación, despacho, auto,
publicación procesal). Son estructuras inmutables salvo `ProcesoVigilado`, que representa
estado persistido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


@dataclass(frozen=True)
class Proceso:
    """Resumen de un proceso tal como lo devuelve la búsqueda por radicado.

    Un mismo radicado puede aparecer en varios despachos (por ejemplo, primera
    instancia y tribunal en apelación); cada aparición tiene su propio `id_proceso`.
    """

    id_proceso: int
    radicado: str
    despacho: str = ""
    departamento: str = ""
    sujetos: str = ""
    fecha_proceso: date | None = None
    fecha_ultima_actuacion: date | None = None
    es_privado: bool = False


@dataclass(frozen=True)
class DetalleProceso:
    """Ficha ampliada del proceso (ponente, tipo, clase, ubicación)."""

    id_proceso: int
    radicado: str
    despacho: str = ""
    ponente: str = ""
    tipo_proceso: str = ""
    clase_proceso: str = ""
    subclase_proceso: str = ""
    recurso: str = ""
    ubicacion: str = ""
    contenido_radicacion: str = ""
    es_privado: bool = False
    fecha_proceso: date | None = None
    ultima_actualizacion: datetime | None = None
    codigo_despacho: str = ""


@dataclass(frozen=True)
class Actuacion:
    """Una actuación registrada en el expediente electrónico."""

    id_registro: int
    radicado: str
    consecutivo: int
    actuacion: str = ""
    anotacion: str = ""
    fecha_actuacion: date | None = None
    fecha_registro: date | None = None
    fecha_inicial: date | None = None
    fecha_final: date | None = None
    con_documentos: bool = False
    id_proceso: int | None = None

    @property
    def texto(self) -> str:
        partes = [p for p in (self.actuacion.strip(), self.anotacion.strip()) if p]
        return " - ".join(partes)


@dataclass(frozen=True)
class PaginaActuaciones:
    """Una página de actuaciones, en el orden entregado por la fuente (más recientes primero)."""

    actuaciones: tuple[Actuacion, ...]
    pagina: int = 1
    total_paginas: int = 1
    total_registros: int = 0
    registros_por_pagina: int = 0

    @property
    def hay_mas(self) -> bool:
        return self.pagina < self.total_paginas


@dataclass(frozen=True)
class Documento:
    """Un documento adjunto a una actuación (normalmente el PDF del auto o de la providencia)."""

    id_documento: int
    id_registro: int
    nombre: str = ""
    fecha: date | None = None
    tipo: str = ""
    tamano: int | None = None


@dataclass(frozen=True)
class DescargaDocumento:
    """Contenido descargado de un documento."""

    id_documento: int
    contenido: bytes
    nombre: str = ""
    tipo_contenido: str = "application/octet-stream"


@dataclass(frozen=True)
class Novedad:
    """Una actuación evaluada por el detector de autos, con su estado local."""

    radicado: str
    actuacion: Actuacion
    es_auto: bool
    coincidencias: tuple[str, ...] = ()
    despacho: str = ""
    documentos: tuple[Documento, ...] = ()
    visto_en: datetime | None = None
    revisada: bool = False


# --- publicaciones procesales (estados, avisos, traslados...) --------------------------------


@dataclass(frozen=True)
class DocumentoPublicado:
    """Un archivo enlazado desde una publicación procesal (el PDF del estado, el de los autos...)."""

    etiqueta: str
    url: str


@dataclass(frozen=True)
class Publicacion:
    """Una publicación con efectos procesales de un despacho (estado, aviso, traslado, edicto...)."""

    id_publicacion: str
    tipo: str
    despacho_codigo: str
    titulo: str
    url_detalle: str = ""
    despacho: str = ""
    id_estructura: int | None = None
    fecha_publicacion: date | None = None
    resumen: str = ""
    documentos: tuple[DocumentoPublicado, ...] = ()
    departamento: str = ""
    municipio: str = ""
    entidad: str = ""
    especialidad: str = ""
    analizada: bool = False
    visto_en: datetime | None = None


@dataclass(frozen=True)
class PaginaPublicaciones:
    publicaciones: tuple[Publicacion, ...]
    pagina: int = 1
    por_pagina: int = 75
    total: int = 0

    @property
    def hay_mas(self) -> bool:
        return self.pagina * self.por_pagina < self.total


@dataclass(frozen=True)
class CoincidenciaPublicacion:
    """Un radicado vigilado mencionado en una publicación (en el título, el resumen o un PDF)."""

    publicacion: Publicacion
    radicado: str
    forma: str
    donde: str
    fragmento: str = ""
    id: int | None = None
    visto_en: datetime | None = None
    revisada: bool = False


@dataclass
class ProcesoVigilado:
    """Estado persistido de un radicado bajo vigilancia."""

    radicado: str
    alias: str | None = None
    id_proceso: int | None = None
    huella: str | None = None
    fecha_ultima_actuacion: date | None = None
    ultima_verificacion: datetime | None = None
    ultima_lectura_actuaciones: datetime | None = None
    inicializado: bool = False
    activo: bool = True
    creado_en: datetime | None = None
    despachos: tuple[str, ...] = ()
    # Ficha del proceso, tomada de la fuente en la línea base y refrescada cuando hay actuaciones nuevas.
    despacho: str = ""
    departamento: str = ""
    sujetos: str = ""
    tipo_proceso: str = ""
    clase_proceso: str = ""
    ponente: str = ""
    fecha_proceso: date | None = None
    ficha_leida_en: datetime | None = None

    @property
    def titulo(self) -> str:
        """Nombre con el que se presenta el proceso: el alias, si lo hay; si no, los sujetos procesales."""
        if self.alias:
            return self.alias
        if self.sujetos:
            return self.sujetos
        return f"Proceso {self.radicado}"

    @property
    def codigos_despacho(self) -> tuple[str, ...]:
        """Códigos de despacho (12 dígitos) a vigilar: el del radicado más los descubiertos en la fuente."""
        codigos = [self.radicado[:12]] if len(self.radicado) >= 12 else []
        for codigo in self.despachos:
            if codigo and codigo not in codigos:
                codigos.append(codigo)
        return tuple(codigos)


class EstadoVerificacion(str, Enum):
    OK = "OK"
    SIN_CAMBIOS = "SIN_CAMBIOS"
    NO_ENCONTRADO = "NO_ENCONTRADO"
    PRIVADO = "PRIVADO"
    ERROR = "ERROR"
    OMITIDO = "OMITIDO"


@dataclass
class ResultadoVerificacion:
    """Resultado de verificar un radicado en una corrida."""

    radicado: str
    estado: EstadoVerificacion
    momento: datetime
    procesos: list[Proceso] = field(default_factory=list)
    novedades: list[Novedad] = field(default_factory=list)
    mensaje: str = ""
    solicitudes: int = 0
    es_linea_base: bool = False
    publicaciones: list[CoincidenciaPublicacion] = field(default_factory=list)

    @property
    def proceso(self) -> Proceso | None:
        return self.procesos[0] if self.procesos else None

    @property
    def autos(self) -> list[Novedad]:
        return [n for n in self.novedades if n.es_auto]


@dataclass
class ResultadoPublicaciones:
    """Resultado de revisar las publicaciones de un despacho."""

    despacho_codigo: str
    estado: EstadoVerificacion
    momento: datetime
    despacho: str = ""
    nuevas: int = 0
    coincidencias: list[CoincidenciaPublicacion] = field(default_factory=list)
    mensaje: str = ""
    solicitudes: int = 0


@dataclass
class EventoNovedades:
    """Lo que reciben los notificadores cuando aparecen actuaciones o publicaciones nuevas."""

    radicado: str
    alias: str | None
    procesos: list[Proceso]
    novedades: list[Novedad]
    momento: datetime
    es_linea_base: bool = False
    publicaciones: list[CoincidenciaPublicacion] = field(default_factory=list)

    @property
    def autos(self) -> list[Novedad]:
        return [n for n in self.novedades if n.es_auto]

    @property
    def despacho(self) -> str:
        if self.procesos:
            return self.procesos[0].despacho
        if self.publicaciones:
            return self.publicaciones[0].publicacion.despacho
        return ""


@dataclass
class ConsultaProceso:
    """Resultado de una consulta puntual (sin persistencia)."""

    radicado: str
    procesos: list[Proceso]
    detalles: list[DetalleProceso]
    novedades: list[Novedad]

    @property
    def actuaciones(self) -> list[Actuacion]:
        return [n.actuacion for n in self.novedades]

    @property
    def autos(self) -> list[Novedad]:
        return [n for n in self.novedades if n.es_auto]
