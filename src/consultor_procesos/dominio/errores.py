"""Excepciones propias del consultor."""

from __future__ import annotations


class ErrorConsultor(Exception):
    """Base de todos los errores propios del consultor."""


class RadicadoInvalido(ErrorConsultor, ValueError):
    """El número de radicación no cumple el formato de 23 dígitos."""


class ProcesoNoEncontrado(ErrorConsultor):
    """La fuente no devolvió ningún proceso para el radicado."""


class ErrorFuente(ErrorConsultor):
    """La fuente respondió con un error definitivo (4xx) o con un cuerpo inesperado."""

    def __init__(self, mensaje: str, codigo: int | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class RespuestaInesperada(ErrorFuente):
    """La fuente respondió, pero con una estructura distinta a la conocida (campo renombrado o ausente).

    No se tolera en silencio: una clave renombrada haría que el programa dejara de ver
    actuaciones sin avisar. La verificación queda en ERROR y el monitor lo reporta.
    """


class FuenteNoDisponible(ErrorFuente):
    """La fuente no respondió correctamente tras agotar los reintentos."""


class CircuitoAbierto(FuenteNoDisponible):
    """El cortacircuito está abierto: las consultas se suspenden temporalmente."""


class PresupuestoAgotado(ErrorConsultor):
    """Se alcanzó el máximo de solicitudes permitidas para el día."""


class DocumentoNoEncontrado(ErrorConsultor):
    """El documento solicitado no está registrado localmente o la fuente no lo conoce."""
