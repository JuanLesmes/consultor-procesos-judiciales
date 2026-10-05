"""Adaptador para la Consulta de Procesos Nacional Unificada (CPNU) de la Rama Judicial."""

from .cliente import AGENTE_USUARIO_PREDETERMINADO, URL_BASE_CPNU, ClienteCPNU

__all__ = ["ClienteCPNU", "URL_BASE_CPNU", "AGENTE_USUARIO_PREDETERMINADO"]
