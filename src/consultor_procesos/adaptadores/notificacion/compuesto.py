"""Reparte un evento entre varios notificadores aislando los fallos de cada uno."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from ...dominio.modelos import EventoNovedades
from ...dominio.puertos import Notificador

log = logging.getLogger(__name__)


class NotificadorCompuesto:
    def __init__(self, notificadores: Iterable[Notificador]) -> None:
        self.notificadores = list(notificadores)
        self.errores: list[tuple[str, Exception]] = []

    def notificar(self, evento: EventoNovedades) -> None:
        for notificador in self.notificadores:
            try:
                notificador.notificar(evento)
            except Exception as exc:  # noqa: BLE001 - un notificador caído no debe frenar a los demás
                nombre = type(notificador).__name__
                self.errores.append((nombre, exc))
                log.exception("El notificador %s falló al procesar el radicado %s", nombre, evento.radicado)
