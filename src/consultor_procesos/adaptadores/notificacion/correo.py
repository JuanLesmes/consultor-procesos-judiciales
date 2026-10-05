"""Notificador por correo electrónico (SMTP con STARTTLS). El transporte es inyectable para pruebas."""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage

from ...dominio.modelos import EventoNovedades
from .formato import asunto_evento, evento_tiene_contenido, formatear_evento

Transporte = Callable[[EmailMessage], None]


@dataclass
class ConfiguracionCorreo:
    servidor: str
    puerto: int = 587
    usuario: str = ""
    contrasena: str = ""
    remitente: str = ""
    destinatarios: tuple[str, ...] = ()
    usar_tls: bool = True
    solo_autos: bool = True
    tiempo_espera: float = 30.0


class NotificadorCorreo:
    def __init__(self, config: ConfiguracionCorreo, transporte: Transporte | None = None) -> None:
        if not config.destinatarios:
            raise ValueError("El notificador de correo necesita al menos un destinatario.")
        self._config = config
        self._transporte = transporte or self._enviar_smtp

    def construir_mensaje(self, evento: EventoNovedades) -> EmailMessage | None:
        if not evento_tiene_contenido(evento, self._config.solo_autos):
            return None
        mensaje = EmailMessage()
        mensaje["Subject"] = asunto_evento(evento)
        mensaje["From"] = self._config.remitente or self._config.usuario
        mensaje["To"] = ", ".join(self._config.destinatarios)
        cuerpo = formatear_evento(evento, solo_autos=self._config.solo_autos)
        cuerpo += (
            "\n\nConsulte el expediente en https://consultaprocesos.ramajudicial.gov.co/\n"
            "Mensaje generado automáticamente por Consultor de Procesos."
        )
        mensaje.set_content(cuerpo)
        return mensaje

    def notificar(self, evento: EventoNovedades) -> None:
        mensaje = self.construir_mensaje(evento)
        if mensaje is not None:
            self._transporte(mensaje)

    def _enviar_smtp(self, mensaje: EmailMessage) -> None:
        with smtplib.SMTP(self._config.servidor, self._config.puerto, timeout=self._config.tiempo_espera) as smtp:
            if self._config.usar_tls:
                smtp.starttls()
            if self._config.usuario:
                smtp.login(self._config.usuario, self._config.contrasena)
            smtp.send_message(mensaje)
