"""Repositorio SQLite (biblioteca estándar, sin ORM). Un archivo, ocho tablas.

Todas las operaciones se serializan con un candado reentrante porque la interfaz web
comparte la conexión entre el hilo del servidor y el de la verificación en segundo plano.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from ...dominio.modelos import (
    Actuacion,
    CoincidenciaPublicacion,
    Documento,
    DocumentoPublicado,
    Novedad,
    ProcesoVigilado,
    Publicacion,
    ResultadoVerificacion,
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS procesos_vigilados (
    radicado TEXT PRIMARY KEY,
    alias TEXT,
    id_proceso INTEGER,
    huella TEXT,
    fecha_ultima_actuacion TEXT,
    ultima_verificacion TEXT,
    ultima_lectura_actuaciones TEXT,
    inicializado INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    creado_en TEXT,
    despachos TEXT,
    despacho TEXT,
    departamento TEXT,
    sujetos TEXT,
    tipo_proceso TEXT,
    clase_proceso TEXT,
    ponente TEXT,
    fecha_proceso TEXT,
    ficha_leida_en TEXT
);
CREATE TABLE IF NOT EXISTS actuaciones_vistas (
    id_registro INTEGER PRIMARY KEY,
    radicado TEXT NOT NULL,
    id_proceso INTEGER,
    consecutivo INTEGER NOT NULL DEFAULT 0,
    actuacion TEXT,
    anotacion TEXT,
    fecha_actuacion TEXT,
    fecha_registro TEXT,
    fecha_inicial TEXT,
    fecha_final TEXT,
    con_documentos INTEGER NOT NULL DEFAULT 0,
    es_auto INTEGER NOT NULL DEFAULT 0,
    coincidencias TEXT,
    despacho TEXT,
    visto_en TEXT NOT NULL,
    revisada INTEGER NOT NULL DEFAULT 0,
    documentos_consultados_en TEXT
);
CREATE INDEX IF NOT EXISTS idx_actuaciones_radicado ON actuaciones_vistas (radicado, consecutivo);
CREATE INDEX IF NOT EXISTS idx_actuaciones_visto ON actuaciones_vistas (visto_en);
CREATE TABLE IF NOT EXISTS documentos (
    id_documento INTEGER PRIMARY KEY,
    id_registro INTEGER NOT NULL,
    nombre TEXT,
    fecha TEXT,
    tipo TEXT,
    tamano INTEGER,
    ruta_local TEXT,
    descargado_en TEXT,
    registrado_en TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documentos_registro ON documentos (id_registro);
CREATE TABLE IF NOT EXISTS publicaciones (
    id_publicacion TEXT PRIMARY KEY,
    despacho_codigo TEXT NOT NULL,
    despacho TEXT,
    tipo TEXT,
    id_estructura INTEGER,
    titulo TEXT,
    fecha_publicacion TEXT,
    url_detalle TEXT,
    resumen TEXT,
    documentos TEXT,
    departamento TEXT,
    municipio TEXT,
    entidad TEXT,
    especialidad TEXT,
    analizada INTEGER NOT NULL DEFAULT 0,
    visto_en TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publicaciones_despacho ON publicaciones (despacho_codigo, fecha_publicacion);
CREATE TABLE IF NOT EXISTS publicaciones_coincidencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_publicacion TEXT NOT NULL,
    radicado TEXT NOT NULL,
    forma TEXT,
    donde TEXT,
    fragmento TEXT,
    visto_en TEXT NOT NULL,
    revisada INTEGER NOT NULL DEFAULT 0,
    UNIQUE (id_publicacion, radicado, donde)
);
CREATE INDEX IF NOT EXISTS idx_coincidencias_radicado ON publicaciones_coincidencias (radicado, visto_en);
CREATE TABLE IF NOT EXISTS revisiones_despacho (
    despacho_codigo TEXT PRIMARY KEY,
    ultima_revision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verificaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    radicado TEXT NOT NULL,
    momento TEXT NOT NULL,
    estado TEXT NOT NULL,
    novedades INTEGER NOT NULL DEFAULT 0,
    autos INTEGER NOT NULL DEFAULT 0,
    solicitudes INTEGER NOT NULL DEFAULT 0,
    mensaje TEXT
);
CREATE INDEX IF NOT EXISTS idx_verificaciones_radicado ON verificaciones (radicado, momento);
CREATE TABLE IF NOT EXISTS contadores_solicitudes (
    fecha TEXT PRIMARY KEY,
    cantidad INTEGER NOT NULL DEFAULT 0
);
"""

# Columnas añadidas después de la primera versión: se agregan a bases existentes al abrirlas.
MIGRACIONES: dict[str, dict[str, str]] = {
    "actuaciones_vistas": {"revisada": "INTEGER NOT NULL DEFAULT 0", "documentos_consultados_en": "TEXT"},
    "procesos_vigilados": {
        "despachos": "TEXT",
        "despacho": "TEXT",
        "departamento": "TEXT",
        "sujetos": "TEXT",
        "tipo_proceso": "TEXT",
        "clase_proceso": "TEXT",
        "ponente": "TEXT",
        "fecha_proceso": "TEXT",
        "ficha_leida_en": "TEXT",
    },
}


def _a_iso(valor: date | datetime | None) -> str | None:
    return valor.isoformat() if valor is not None else None


def _a_fecha(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def _a_fecha_hora(texto: str | None) -> datetime | None:
    return datetime.fromisoformat(texto) if texto else None


def _sincronizado(metodo: Callable) -> Callable:
    @wraps(metodo)
    def envoltura(self: "RepositorioSQLite", *args: Any, **kwargs: Any) -> Any:
        with self._candado:
            return metodo(self, *args, **kwargs)

    return envoltura


class RepositorioSQLite:
    def __init__(self, ruta: str | Path = "consultor_procesos.sqlite") -> None:
        self.ruta = str(ruta)
        self._candado = threading.RLock()
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        self._conexion = sqlite3.connect(self.ruta, check_same_thread=False)
        self._conexion.row_factory = sqlite3.Row
        if self.ruta != ":memory:":
            self._conexion.execute("PRAGMA journal_mode=WAL")
        self._conexion.executescript(ESQUEMA)
        self._migrar()
        self._conexion.commit()

    def _migrar(self) -> None:
        for tabla, columnas in MIGRACIONES.items():
            existentes = {fila["name"] for fila in self._conexion.execute(f"PRAGMA table_info({tabla})")}
            for columna, definicion in columnas.items():
                if columna not in existentes:
                    self._conexion.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")

    def cerrar(self) -> None:
        with self._candado:
            self._conexion.close()

    def __enter__(self) -> "RepositorioSQLite":
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()

    # --- vigilados ---------------------------------------------------------------------

    @_sincronizado
    def guardar_vigilado(self, vigilado: ProcesoVigilado) -> None:
        self._conexion.execute(
            """
            INSERT INTO procesos_vigilados (
                radicado, alias, id_proceso, huella, fecha_ultima_actuacion, ultima_verificacion,
                ultima_lectura_actuaciones, inicializado, activo, creado_en, despachos,
                despacho, departamento, sujetos, tipo_proceso, clase_proceso, ponente, fecha_proceso,
                ficha_leida_en
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(radicado) DO UPDATE SET
                alias = excluded.alias,
                id_proceso = excluded.id_proceso,
                huella = excluded.huella,
                fecha_ultima_actuacion = excluded.fecha_ultima_actuacion,
                ultima_verificacion = excluded.ultima_verificacion,
                ultima_lectura_actuaciones = excluded.ultima_lectura_actuaciones,
                inicializado = excluded.inicializado,
                activo = excluded.activo,
                creado_en = COALESCE(procesos_vigilados.creado_en, excluded.creado_en),
                despachos = excluded.despachos,
                despacho = excluded.despacho,
                departamento = excluded.departamento,
                sujetos = excluded.sujetos,
                tipo_proceso = excluded.tipo_proceso,
                clase_proceso = excluded.clase_proceso,
                ponente = excluded.ponente,
                fecha_proceso = excluded.fecha_proceso,
                ficha_leida_en = excluded.ficha_leida_en
            """,
            (
                vigilado.radicado,
                vigilado.alias,
                vigilado.id_proceso,
                vigilado.huella,
                _a_iso(vigilado.fecha_ultima_actuacion),
                _a_iso(vigilado.ultima_verificacion),
                _a_iso(vigilado.ultima_lectura_actuaciones),
                1 if vigilado.inicializado else 0,
                1 if vigilado.activo else 0,
                _a_iso(vigilado.creado_en),
                ",".join(vigilado.despachos),
                vigilado.despacho,
                vigilado.departamento,
                vigilado.sujetos,
                vigilado.tipo_proceso,
                vigilado.clase_proceso,
                vigilado.ponente,
                _a_iso(vigilado.fecha_proceso),
                _a_iso(vigilado.ficha_leida_en),
            ),
        )
        self._conexion.commit()

    @staticmethod
    def _fila_a_vigilado(fila: sqlite3.Row) -> ProcesoVigilado:
        despachos = tuple(d for d in (fila["despachos"] or "").split(",") if d)
        return ProcesoVigilado(
            radicado=fila["radicado"],
            alias=fila["alias"],
            id_proceso=fila["id_proceso"],
            huella=fila["huella"],
            fecha_ultima_actuacion=_a_fecha(fila["fecha_ultima_actuacion"]),
            ultima_verificacion=_a_fecha_hora(fila["ultima_verificacion"]),
            ultima_lectura_actuaciones=_a_fecha_hora(fila["ultima_lectura_actuaciones"]),
            inicializado=bool(fila["inicializado"]),
            activo=bool(fila["activo"]),
            creado_en=_a_fecha_hora(fila["creado_en"]),
            despachos=despachos,
            despacho=fila["despacho"] or "",
            departamento=fila["departamento"] or "",
            sujetos=fila["sujetos"] or "",
            tipo_proceso=fila["tipo_proceso"] or "",
            clase_proceso=fila["clase_proceso"] or "",
            ponente=fila["ponente"] or "",
            fecha_proceso=_a_fecha(fila["fecha_proceso"]),
            ficha_leida_en=_a_fecha_hora(fila["ficha_leida_en"]),
        )

    @_sincronizado
    def obtener_vigilado(self, radicado: str) -> ProcesoVigilado | None:
        fila = self._conexion.execute(
            "SELECT * FROM procesos_vigilados WHERE radicado = ?", (radicado,)
        ).fetchone()
        return self._fila_a_vigilado(fila) if fila else None

    @_sincronizado
    def listar_vigilados(self, solo_activos: bool = True) -> list[ProcesoVigilado]:
        consulta = "SELECT * FROM procesos_vigilados"
        if solo_activos:
            consulta += " WHERE activo = 1"
        consulta += " ORDER BY radicado"
        return [self._fila_a_vigilado(f) for f in self._conexion.execute(consulta)]

    @_sincronizado
    def eliminar_vigilado(self, radicado: str) -> bool:
        cursor = self._conexion.execute("DELETE FROM procesos_vigilados WHERE radicado = ?", (radicado,))
        self._conexion.execute(
            "DELETE FROM documentos WHERE id_registro IN (SELECT id_registro FROM actuaciones_vistas WHERE radicado = ?)",
            (radicado,),
        )
        self._conexion.execute("DELETE FROM actuaciones_vistas WHERE radicado = ?", (radicado,))
        self._conexion.execute("DELETE FROM publicaciones_coincidencias WHERE radicado = ?", (radicado,))
        self._conexion.commit()
        return cursor.rowcount > 0

    # --- actuaciones y novedades -------------------------------------------------------

    @_sincronizado
    def ids_actuaciones_conocidas(self, radicado: str) -> set[int]:
        filas = self._conexion.execute(
            "SELECT id_registro FROM actuaciones_vistas WHERE radicado = ?", (radicado,)
        )
        return {fila["id_registro"] for fila in filas}

    @_sincronizado
    def guardar_novedades(self, novedades: Iterable[Novedad], momento: datetime) -> int:
        insertadas = 0
        for novedad in novedades:
            a = novedad.actuacion
            cursor = self._conexion.execute(
                """
                INSERT OR IGNORE INTO actuaciones_vistas (
                    id_registro, radicado, id_proceso, consecutivo, actuacion, anotacion,
                    fecha_actuacion, fecha_registro, fecha_inicial, fecha_final,
                    con_documentos, es_auto, coincidencias, despacho, visto_en, revisada
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a.id_registro,
                    novedad.radicado or a.radicado,
                    a.id_proceso,
                    a.consecutivo,
                    a.actuacion,
                    a.anotacion,
                    _a_iso(a.fecha_actuacion),
                    _a_iso(a.fecha_registro),
                    _a_iso(a.fecha_inicial),
                    _a_iso(a.fecha_final),
                    1 if a.con_documentos else 0,
                    1 if novedad.es_auto else 0,
                    ",".join(novedad.coincidencias),
                    novedad.despacho,
                    momento.isoformat(),
                    1 if novedad.revisada else 0,
                ),
            )
            if cursor.rowcount > 0:
                insertadas += 1
                if novedad.documentos:
                    self._guardar_documentos(a.id_registro, novedad.documentos, momento)
        self._conexion.commit()
        return insertadas

    def _documentos_por_registro(self, ids: list[int]) -> dict[int, list[Documento]]:
        if not ids:
            return {}
        resultado: dict[int, list[Documento]] = {}
        for inicio in range(0, len(ids), 500):
            lote = ids[inicio : inicio + 500]
            marcadores = ",".join("?" * len(lote))
            filas = self._conexion.execute(
                f"SELECT * FROM documentos WHERE id_registro IN ({marcadores}) ORDER BY id_documento", lote
            )
            for fila in filas:
                resultado.setdefault(fila["id_registro"], []).append(self._fila_a_documento(fila))
        return resultado

    @staticmethod
    def _fila_a_documento(fila: sqlite3.Row) -> Documento:
        return Documento(
            id_documento=fila["id_documento"],
            id_registro=fila["id_registro"],
            nombre=fila["nombre"] or "",
            fecha=_a_fecha(fila["fecha"]),
            tipo=fila["tipo"] or "",
            tamano=fila["tamano"],
        )

    @staticmethod
    def _fila_a_novedad(fila: sqlite3.Row, documentos: list[Documento]) -> Novedad:
        actuacion = Actuacion(
            id_registro=fila["id_registro"],
            radicado=fila["radicado"],
            consecutivo=fila["consecutivo"],
            actuacion=fila["actuacion"] or "",
            anotacion=fila["anotacion"] or "",
            fecha_actuacion=_a_fecha(fila["fecha_actuacion"]),
            fecha_registro=_a_fecha(fila["fecha_registro"]),
            fecha_inicial=_a_fecha(fila["fecha_inicial"]),
            fecha_final=_a_fecha(fila["fecha_final"]),
            con_documentos=bool(fila["con_documentos"]),
            id_proceso=fila["id_proceso"],
        )
        coincidencias = tuple(c for c in (fila["coincidencias"] or "").split(",") if c)
        return Novedad(
            radicado=fila["radicado"],
            actuacion=actuacion,
            es_auto=bool(fila["es_auto"]),
            coincidencias=coincidencias,
            despacho=fila["despacho"] or "",
            documentos=tuple(documentos),
            visto_en=_a_fecha_hora(fila["visto_en"]),
            revisada=bool(fila["revisada"]),
        )

    def _filas_a_novedades(self, filas: Iterable[sqlite3.Row]) -> list[Novedad]:
        lista = list(filas)
        documentos = self._documentos_por_registro([f["id_registro"] for f in lista])
        return [self._fila_a_novedad(f, documentos.get(f["id_registro"], [])) for f in lista]

    @_sincronizado
    def listar_actuaciones(self, radicado: str, solo_autos: bool = False) -> list[Novedad]:
        consulta = "SELECT * FROM actuaciones_vistas WHERE radicado = ?"
        if solo_autos:
            consulta += " AND es_auto = 1"
        consulta += " ORDER BY consecutivo, id_registro"
        return self._filas_a_novedades(self._conexion.execute(consulta, (radicado,)))

    @_sincronizado
    def obtener_novedad(self, id_registro: int) -> Novedad | None:
        filas = self._conexion.execute("SELECT * FROM actuaciones_vistas WHERE id_registro = ?", (id_registro,))
        novedades = self._filas_a_novedades(filas)
        return novedades[0] if novedades else None

    @_sincronizado
    def listar_novedades_recientes(
        self,
        limite: int = 50,
        solo_autos: bool = False,
        radicado: str | None = None,
        solo_pendientes: bool = False,
    ) -> list[Novedad]:
        condiciones = ["1 = 1"]
        parametros: list[Any] = []
        if solo_autos:
            condiciones.append("es_auto = 1")
        if radicado:
            condiciones.append("radicado = ?")
            parametros.append(radicado)
        if solo_pendientes:
            condiciones.append("revisada = 0")
        parametros.append(max(1, limite))
        consulta = (
            "SELECT * FROM actuaciones_vistas WHERE "
            + " AND ".join(condiciones)
            + " ORDER BY visto_en DESC, fecha_actuacion DESC, consecutivo DESC, id_registro DESC LIMIT ?"
        )
        return self._filas_a_novedades(self._conexion.execute(consulta, parametros))

    @_sincronizado
    def contar_pendientes(self, solo_autos: bool = False) -> int:
        consulta = "SELECT COUNT(*) AS n FROM actuaciones_vistas WHERE revisada = 0"
        if solo_autos:
            consulta += " AND es_auto = 1"
        return int(self._conexion.execute(consulta).fetchone()["n"])

    @_sincronizado
    def marcar_revisada(self, id_registro: int, revisada: bool = True) -> bool:
        cursor = self._conexion.execute(
            "UPDATE actuaciones_vistas SET revisada = ? WHERE id_registro = ?",
            (1 if revisada else 0, id_registro),
        )
        self._conexion.commit()
        return cursor.rowcount > 0

    # --- documentos ----------------------------------------------------------------------

    def _guardar_documentos(self, id_registro: int, documentos: Iterable[Documento], momento: datetime) -> None:
        for documento in documentos:
            self._conexion.execute(
                """
                INSERT INTO documentos (id_documento, id_registro, nombre, fecha, tipo, tamano, registrado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_documento) DO UPDATE SET
                    id_registro = excluded.id_registro,
                    nombre = excluded.nombre,
                    fecha = excluded.fecha,
                    tipo = excluded.tipo,
                    tamano = excluded.tamano
                """,
                (
                    documento.id_documento,
                    id_registro,
                    documento.nombre,
                    _a_iso(documento.fecha),
                    documento.tipo,
                    documento.tamano,
                    momento.isoformat(),
                ),
            )
        self._conexion.execute(
            "UPDATE actuaciones_vistas SET documentos_consultados_en = ? WHERE id_registro = ?",
            (momento.isoformat(), id_registro),
        )

    @_sincronizado
    def guardar_documentos(self, id_registro: int, documentos: Iterable[Documento], momento: datetime) -> None:
        self._guardar_documentos(id_registro, documentos, momento)
        self._conexion.commit()

    @_sincronizado
    def listar_documentos(self, id_registro: int) -> list[Documento] | None:
        filas = self._conexion.execute(
            "SELECT * FROM documentos WHERE id_registro = ? ORDER BY id_documento", (id_registro,)
        ).fetchall()
        if filas:
            return [self._fila_a_documento(f) for f in filas]
        fila = self._conexion.execute(
            "SELECT documentos_consultados_en FROM actuaciones_vistas WHERE id_registro = ?", (id_registro,)
        ).fetchone()
        if fila is not None and fila["documentos_consultados_en"]:
            return []
        return None

    @_sincronizado
    def obtener_documento(self, id_documento: int) -> Documento | None:
        fila = self._conexion.execute("SELECT * FROM documentos WHERE id_documento = ?", (id_documento,)).fetchone()
        return self._fila_a_documento(fila) if fila else None

    @_sincronizado
    def registrar_descarga(self, id_documento: int, ruta_local: str, momento: datetime) -> None:
        self._conexion.execute(
            "UPDATE documentos SET ruta_local = ?, descargado_en = ? WHERE id_documento = ?",
            (ruta_local, momento.isoformat(), id_documento),
        )
        self._conexion.commit()

    @_sincronizado
    def ruta_local_documento(self, id_documento: int) -> str | None:
        fila = self._conexion.execute(
            "SELECT ruta_local FROM documentos WHERE id_documento = ?", (id_documento,)
        ).fetchone()
        return fila["ruta_local"] if fila and fila["ruta_local"] else None

    # --- publicaciones procesales --------------------------------------------------------

    @staticmethod
    def _fila_a_publicacion(fila: sqlite3.Row) -> Publicacion:
        documentos = tuple(
            DocumentoPublicado(etiqueta=d.get("etiqueta", ""), url=d.get("url", ""))
            for d in json.loads(fila["documentos"] or "[]")
        )
        return Publicacion(
            id_publicacion=fila["id_publicacion"],
            tipo=fila["tipo"] or "",
            despacho_codigo=fila["despacho_codigo"],
            titulo=fila["titulo"] or "",
            url_detalle=fila["url_detalle"] or "",
            despacho=fila["despacho"] or "",
            id_estructura=fila["id_estructura"],
            fecha_publicacion=_a_fecha(fila["fecha_publicacion"]),
            resumen=fila["resumen"] or "",
            documentos=documentos,
            departamento=fila["departamento"] or "",
            municipio=fila["municipio"] or "",
            entidad=fila["entidad"] or "",
            especialidad=fila["especialidad"] or "",
            analizada=bool(fila["analizada"]),
            visto_en=_a_fecha_hora(fila["visto_en"]),
        )

    @_sincronizado
    def ids_publicaciones_conocidas(self, despacho_codigo: str) -> set[str]:
        filas = self._conexion.execute(
            "SELECT id_publicacion FROM publicaciones WHERE despacho_codigo = ?", (despacho_codigo,)
        )
        return {fila["id_publicacion"] for fila in filas}

    @_sincronizado
    def guardar_publicaciones(self, publicaciones: Iterable[Publicacion], momento: datetime) -> int:
        insertadas = 0
        for p in publicaciones:
            existia = self._conexion.execute(
                "SELECT 1 FROM publicaciones WHERE id_publicacion = ?", (p.id_publicacion,)
            ).fetchone() is not None
            self._conexion.execute(
                """
                INSERT INTO publicaciones (
                    id_publicacion, despacho_codigo, despacho, tipo, id_estructura, titulo, fecha_publicacion,
                    url_detalle, resumen, documentos, departamento, municipio, entidad, especialidad, analizada, visto_en
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_publicacion) DO UPDATE SET
                    analizada = MAX(publicaciones.analizada, excluded.analizada),
                    resumen = CASE WHEN excluded.resumen <> '' THEN excluded.resumen ELSE publicaciones.resumen END,
                    documentos = CASE WHEN excluded.documentos <> '[]' THEN excluded.documentos ELSE publicaciones.documentos END
                """,
                (
                    p.id_publicacion,
                    p.despacho_codigo,
                    p.despacho,
                    p.tipo,
                    p.id_estructura,
                    p.titulo,
                    _a_iso(p.fecha_publicacion),
                    p.url_detalle,
                    p.resumen,
                    json.dumps([{"etiqueta": d.etiqueta, "url": d.url} for d in p.documentos], ensure_ascii=False),
                    p.departamento,
                    p.municipio,
                    p.entidad,
                    p.especialidad,
                    1 if p.analizada else 0,
                    (p.visto_en or momento).isoformat(),
                ),
            )
            if not existia:
                insertadas += 1
        self._conexion.commit()
        return insertadas

    @_sincronizado
    def listar_publicaciones(self, despacho_codigo: str | None = None, limite: int = 50) -> list[Publicacion]:
        if despacho_codigo:
            filas = self._conexion.execute(
                "SELECT * FROM publicaciones WHERE despacho_codigo = ? ORDER BY fecha_publicacion DESC, visto_en DESC LIMIT ?",
                (despacho_codigo, max(1, limite)),
            )
        else:
            filas = self._conexion.execute(
                "SELECT * FROM publicaciones ORDER BY fecha_publicacion DESC, visto_en DESC LIMIT ?", (max(1, limite),)
            )
        return [self._fila_a_publicacion(f) for f in filas]

    @_sincronizado
    def obtener_publicacion(self, id_publicacion: str) -> Publicacion | None:
        fila = self._conexion.execute(
            "SELECT * FROM publicaciones WHERE id_publicacion = ?", (id_publicacion,)
        ).fetchone()
        return self._fila_a_publicacion(fila) if fila else None

    @_sincronizado
    def guardar_coincidencias(self, coincidencias: Iterable[CoincidenciaPublicacion], momento: datetime) -> int:
        insertadas = 0
        for c in coincidencias:
            cursor = self._conexion.execute(
                """
                INSERT OR IGNORE INTO publicaciones_coincidencias
                    (id_publicacion, radicado, forma, donde, fragmento, visto_en, revisada)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c.publicacion.id_publicacion,
                    c.radicado,
                    c.forma,
                    c.donde,
                    c.fragmento,
                    (c.visto_en or momento).isoformat(),
                    1 if c.revisada else 0,
                ),
            )
            if cursor.rowcount > 0:
                insertadas += 1
        self._conexion.commit()
        return insertadas

    def _filas_a_coincidencias(self, filas: Iterable[sqlite3.Row]) -> list[CoincidenciaPublicacion]:
        resultado: list[CoincidenciaPublicacion] = []
        publicaciones: dict[str, Publicacion] = {}
        for fila in filas:
            identificador = fila["id_publicacion"]
            if identificador not in publicaciones:
                registro = self._conexion.execute(
                    "SELECT * FROM publicaciones WHERE id_publicacion = ?", (identificador,)
                ).fetchone()
                if registro is None:
                    continue
                publicaciones[identificador] = self._fila_a_publicacion(registro)
            resultado.append(
                CoincidenciaPublicacion(
                    publicacion=publicaciones[identificador],
                    radicado=fila["radicado"],
                    forma=fila["forma"] or "",
                    donde=fila["donde"] or "",
                    fragmento=fila["fragmento"] or "",
                    id=fila["id"],
                    visto_en=_a_fecha_hora(fila["visto_en"]),
                    revisada=bool(fila["revisada"]),
                )
            )
        return resultado

    @_sincronizado
    def listar_coincidencias(
        self, radicado: str | None = None, limite: int = 50, solo_pendientes: bool = False
    ) -> list[CoincidenciaPublicacion]:
        condiciones = ["1 = 1"]
        parametros: list[Any] = []
        if radicado:
            condiciones.append("radicado = ?")
            parametros.append(radicado)
        if solo_pendientes:
            condiciones.append("revisada = 0")
        parametros.append(max(1, limite))
        filas = self._conexion.execute(
            "SELECT * FROM publicaciones_coincidencias WHERE "
            + " AND ".join(condiciones)
            + " ORDER BY visto_en DESC, id DESC LIMIT ?",
            parametros,
        ).fetchall()
        return self._filas_a_coincidencias(filas)

    @_sincronizado
    def contar_coincidencias_pendientes(self) -> int:
        return int(
            self._conexion.execute("SELECT COUNT(*) AS n FROM publicaciones_coincidencias WHERE revisada = 0").fetchone()["n"]
        )

    @_sincronizado
    def marcar_coincidencia_revisada(self, id_coincidencia: int, revisada: bool = True) -> bool:
        cursor = self._conexion.execute(
            "UPDATE publicaciones_coincidencias SET revisada = ? WHERE id = ?", (1 if revisada else 0, id_coincidencia)
        )
        self._conexion.commit()
        return cursor.rowcount > 0

    @_sincronizado
    def obtener_revision_despacho(self, despacho_codigo: str) -> datetime | None:
        fila = self._conexion.execute(
            "SELECT ultima_revision FROM revisiones_despacho WHERE despacho_codigo = ?", (despacho_codigo,)
        ).fetchone()
        return _a_fecha_hora(fila["ultima_revision"]) if fila else None

    @_sincronizado
    def registrar_revision_despacho(self, despacho_codigo: str, momento: datetime) -> None:
        self._conexion.execute(
            """
            INSERT INTO revisiones_despacho (despacho_codigo, ultima_revision) VALUES (?, ?)
            ON CONFLICT(despacho_codigo) DO UPDATE SET ultima_revision = excluded.ultima_revision
            """,
            (despacho_codigo, momento.isoformat()),
        )
        self._conexion.commit()

    # --- verificaciones ------------------------------------------------------------------

    @_sincronizado
    def registrar_verificacion(self, resultado: ResultadoVerificacion) -> None:
        self._conexion.execute(
            """
            INSERT INTO verificaciones (radicado, momento, estado, novedades, autos, solicitudes, mensaje)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resultado.radicado,
                resultado.momento.isoformat(),
                resultado.estado.value,
                len(resultado.novedades),
                len(resultado.autos),
                resultado.solicitudes,
                resultado.mensaje,
            ),
        )
        self._conexion.commit()

    @_sincronizado
    def listar_verificaciones(self, radicado: str | None = None, limite: int = 50) -> list[dict]:
        if radicado is None:
            filas = self._conexion.execute(
                "SELECT * FROM verificaciones ORDER BY id DESC LIMIT ?", (limite,)
            )
        else:
            filas = self._conexion.execute(
                "SELECT * FROM verificaciones WHERE radicado = ? ORDER BY id DESC LIMIT ?",
                (radicado, limite),
            )
        return [
            {
                "radicado": f["radicado"],
                "momento": _a_fecha_hora(f["momento"]),
                "estado": f["estado"],
                "novedades": f["novedades"],
                "autos": f["autos"],
                "solicitudes": f["solicitudes"],
                "mensaje": f["mensaje"] or "",
            }
            for f in filas
        ]

    # --- contadores -----------------------------------------------------------------------

    @_sincronizado
    def obtener_contador(self, fecha: date) -> int:
        fila = self._conexion.execute(
            "SELECT cantidad FROM contadores_solicitudes WHERE fecha = ?", (fecha.isoformat(),)
        ).fetchone()
        return int(fila["cantidad"]) if fila else 0

    @_sincronizado
    def incrementar_contador(self, fecha: date, cantidad: int = 1) -> int:
        self._conexion.execute(
            """
            INSERT INTO contadores_solicitudes (fecha, cantidad) VALUES (?, ?)
            ON CONFLICT(fecha) DO UPDATE SET cantidad = cantidad + excluded.cantidad
            """,
            (fecha.isoformat(), cantidad),
        )
        self._conexion.commit()
        return self.obtener_contador(fecha)
