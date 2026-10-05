"""Reglas de negocio puras: detección de autos, novedades y selección de procesos."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date

from .modelos import Actuacion, Novedad, Proceso

PALABRAS_CLAVE_PREDETERMINADAS: tuple[str, ...] = ("AUTO",)


def normalizar(texto: str | None) -> str:
    """Quita tildes, colapsa espacios y pasa a mayúsculas para comparar texto judicial."""
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sin_acentos).strip().upper()


class DetectorAutos:
    """Decide si una actuación corresponde a un auto (u otra palabra clave configurada).

    Revisa tanto el tipo de actuación como la anotación, porque en la práctica los
    despachos registran autos bajo actuaciones genéricas como "Constancia secretarial"
    y describen el auto en la anotación. La coincidencia es por palabra completa, así
    "AUTOMOTOR" no cuenta pero "AUTO ADMITE", "AUTOS" o "AUTO-ADMISORIO" sí.
    """

    def __init__(self, palabras_clave: Iterable[str] = PALABRAS_CLAVE_PREDETERMINADAS) -> None:
        self._patrones: list[tuple[str, re.Pattern[str]]] = []
        for palabra in palabras_clave:
            clave = normalizar(palabra)
            if not clave:
                continue
            patron = re.compile(r"\b" + re.escape(clave) + r"(?:S|ES)?\b")
            self._patrones.append((clave, patron))

    @property
    def palabras_clave(self) -> tuple[str, ...]:
        return tuple(clave for clave, _ in self._patrones)

    def coincidencias(self, actuacion: Actuacion) -> tuple[str, ...]:
        texto = f"{normalizar(actuacion.actuacion)} | {normalizar(actuacion.anotacion)}"
        return tuple(clave for clave, patron in self._patrones if patron.search(texto))

    def es_auto(self, actuacion: Actuacion) -> bool:
        return bool(self.coincidencias(actuacion))

    def evaluar(self, actuacion: Actuacion, despacho: str = "") -> Novedad:
        coincidencias = self.coincidencias(actuacion)
        return Novedad(
            radicado=actuacion.radicado,
            actuacion=actuacion,
            es_auto=bool(coincidencias),
            coincidencias=coincidencias,
            despacho=despacho,
        )


def ordenar_actuaciones(actuaciones: Iterable[Actuacion]) -> list[Actuacion]:
    """Orden cronológico ascendente (consecutivo, id) para presentar novedades."""
    return sorted(actuaciones, key=lambda a: (a.consecutivo, a.id_registro))


def calcular_novedades(actuaciones: Iterable[Actuacion], ids_conocidos: set[int]) -> list[Actuacion]:
    """Filtra las actuaciones que no se habían visto y las devuelve en orden cronológico."""
    vistos: set[int] = set()
    nuevas: list[Actuacion] = []
    for actuacion in actuaciones:
        if actuacion.id_registro in ids_conocidos or actuacion.id_registro in vistos:
            continue
        vistos.add(actuacion.id_registro)
        nuevas.append(actuacion)
    return ordenar_actuaciones(nuevas)


def seleccionar_procesos(procesos: Iterable[Proceso], radicado: str) -> list[Proceso]:
    """Devuelve los procesos que corresponden al radicado, sin duplicados por id.

    Si la fuente devuelve el mismo radicado en varios despachos se conservan todos:
    cada uno puede tener sus propias actuaciones. Si ninguno coincide exactamente
    (la fuente debería filtrar por radicado) se devuelven tal cual, para no perder datos.
    """
    lista = list(procesos)
    exactos = [p for p in lista if p.radicado == radicado]
    candidatos = exactos or lista
    unicos: dict[int, Proceso] = {}
    for proceso in candidatos:
        unicos.setdefault(proceso.id_proceso, proceso)
    return sorted(
        unicos.values(),
        key=lambda p: (p.fecha_ultima_actuacion or date.min, p.id_proceso),
        reverse=True,
    )


def calcular_huella(procesos: Iterable[Proceso]) -> str:
    """Resumen estable de (id_proceso, fecha de última actuación).

    Permite saber si algo cambió con una sola solicitud de búsqueda, sin descargar actuaciones.
    """
    partes = sorted(
        f"{p.id_proceso}:{p.fecha_ultima_actuacion.isoformat() if p.fecha_ultima_actuacion else '-'}"
        for p in procesos
    )
    return ";".join(partes)


def fecha_ultima_actuacion(procesos: Iterable[Proceso]) -> date | None:
    fechas = [p.fecha_ultima_actuacion for p in procesos if p.fecha_ultima_actuacion]
    return max(fechas) if fechas else None


LONGITUD_RADICADO = 23
_SEPARADORES_RADICADO = re.compile(r"[\s\-.]")


def validar_radicado(valor: str | None) -> str:
    """Normaliza un número de radicación (quita espacios, guiones y puntos) y exige 23 dígitos."""
    from .errores import RadicadoInvalido

    if valor is None:
        raise RadicadoInvalido("El radicado está vacío.")
    limpio = _SEPARADORES_RADICADO.sub("", str(valor))
    if not (limpio.isdigit() and len(limpio) == LONGITUD_RADICADO):
        raise RadicadoInvalido(
            f"El radicado debe tener {LONGITUD_RADICADO} dígitos numéricos; se recibió {str(valor)!r}."
        )
    return limpio


# --- búsqueda de un radicado dentro de textos (estados, avisos, PDF) ----------------------------

_SEPARADORES_ENTRE_DIGITOS = re.compile(r"(?<=\d)[\s\u00a0\-.·/]+(?=\d)")


def normalizar_digitos(texto: str | None) -> str:
    """Une los grupos de dígitos separados por guiones, puntos o espacios: '11001-31-03-001' -> '110013103001'."""
    return _SEPARADORES_ENTRE_DIGITOS.sub("", texto or "")


def patrones_radicado(radicado: str) -> list[tuple[str, re.Pattern[str]]]:
    """Formas en que un radicado aparece escrito en los documentos judiciales, de más a menos precisa.

    Se aplican sobre texto normalizado con `normalizar_digitos`. La forma corta (año y
    consecutivo, p. ej. "2020-00123") solo es fiable dentro de publicaciones del mismo despacho.
    """
    radicado = validar_radicado(radicado)
    anio, consecutivo = radicado[12:16], str(int(radicado[16:21]))
    return [
        ("radicado_completo", re.compile(rf"(?<!\d){radicado}(?!\d)")),
        ("radicado_sin_instancia", re.compile(rf"(?<!\d){radicado[:21]}(?!\d)")),
        ("anio_consecutivo", re.compile(rf"(?<!\d){anio}0{{0,4}}{consecutivo}(?!\d)")),
    ]


def buscar_radicado(texto: str | None, radicado: str, contexto: int = 70) -> tuple[str, str] | None:
    """Devuelve (forma, fragmento) si el radicado aparece en el texto, o None."""
    normalizado = normalizar_digitos(texto)
    if not normalizado:
        return None
    for forma, patron in patrones_radicado(radicado):
        coincidencia = patron.search(normalizado)
        if coincidencia:
            inicio = max(0, coincidencia.start() - contexto)
            fin = min(len(normalizado), coincidencia.end() + contexto)
            fragmento = re.sub(r"\s+", " ", normalizado[inicio:fin]).strip()
            return forma, fragmento
    return None
