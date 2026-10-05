"""Autenticación y protecciones HTTP de la interfaz web en un servidor.

* Usuarios: la variable de entorno `CONSULTOR_USUARIOS` lista `usuario:hash` separados por
  comas. El hash se genera con `consultor-procesos crear-usuario NOMBRE` (scrypt de la
  biblioteca estándar, con sal); la contraseña nunca se guarda.
* Autenticación HTTP Basic: el navegador pide usuario y contraseña. Solo es segura sobre
  HTTPS, que en el despliegue pone Caddy.
* Bloqueo por dirección IP tras varios intentos fallidos seguidos.
* CSRF: las peticiones que modifican algo (POST, DELETE) deben traer la cabecera
  `X-Requested-With: XMLHttpRequest`, que un formulario de otro sitio no puede enviar; y la
  API rechaza lo que el navegador marca como originado en otro sitio (`Sec-Fetch-Site`).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

VARIABLE_USUARIOS = "CONSULTOR_USUARIOS"
CABECERA_CSRF = "X-Requested-With"
VALOR_CSRF = "XMLHttpRequest"
REINO = "Consultor de Procesos"
LONGITUD_MINIMA_CLAVE = 10

_N, _R, _P, _LONGITUD = 2**14, 8, 1, 32


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode("ascii").rstrip("=")


def _de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def generar_hash(clave: str, sal: bytes | None = None) -> str:
    """Hash scrypt con sal en una sola línea sin `$` ni comas (seguro dentro de un archivo .env)."""
    sal = sal if sal is not None else secrets.token_bytes(16)
    derivada = hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=_N, r=_R, p=_P, dklen=_LONGITUD)
    return f"scrypt:{_N}:{_R}:{_P}:{_b64(sal)}:{_b64(derivada)}"


def verificar_hash(clave: str, codificado: str) -> bool:
    try:
        algoritmo, n, r, p, sal, esperado = codificado.split(":")
        if algoritmo != "scrypt":
            return False
        bruto = _de_b64(esperado)
        derivada = hashlib.scrypt(
            clave.encode("utf-8"), salt=_de_b64(sal), n=int(n), r=int(r), p=int(p), dklen=len(bruto)
        )
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(derivada, bruto)


def leer_usuarios(texto: str | None) -> dict[str, str]:
    """Interpreta `usuario:scrypt:...` separados por comas, espacios o saltos de línea."""
    usuarios: dict[str, str] = {}
    for entrada in (texto or "").replace(",", " ").split():
        nombre, separador, codificado = entrada.partition(":")
        if not separador or not nombre or not codificado.startswith("scrypt:"):
            raise ValueError(
                f"Entrada inválida en {VARIABLE_USUARIOS}: {entrada[:20]!r}...; "
                "genere cada usuario con 'consultor-procesos crear-usuario NOMBRE'."
            )
        usuarios[nombre] = codificado
    return usuarios


@dataclass(frozen=True)
class ResultadoAutenticacion:
    usuario: str | None = None
    bloqueado: bool = False
    segundos_bloqueo: int = 0

    @property
    def aceptado(self) -> bool:
        return self.usuario is not None


class Autenticador:
    """Comprueba credenciales HTTP Basic con bloqueo temporal por IP tras fallos seguidos."""

    def __init__(
        self,
        usuarios: dict[str, str],
        max_fallos: int = 10,
        segundos_bloqueo: float = 900.0,
        segundos_cache: float = 600.0,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._usuarios = dict(usuarios)
        self._max_fallos = max_fallos
        self._segundos_bloqueo = segundos_bloqueo
        self._segundos_cache = segundos_cache
        self._reloj = reloj
        self._candado = threading.Lock()
        self._fallos: dict[str, tuple[int, float]] = {}
        # scrypt cuesta ~50 ms a propósito; una credencial ya validada se recuerda un rato
        # (por su huella SHA-256, nunca en claro) para no pagarlo en cada petición.
        self._validadas: dict[bytes, tuple[str, float]] = {}

    @classmethod
    def desde_entorno(cls, entorno: dict[str, str] | None = None) -> Autenticador:
        entorno = os.environ if entorno is None else entorno
        return cls(leer_usuarios(entorno.get(VARIABLE_USUARIOS, "")))

    @property
    def activo(self) -> bool:
        return bool(self._usuarios)

    @property
    def usuarios(self) -> list[str]:
        return sorted(self._usuarios)

    def autenticar(self, cabecera: str | None, ip: str) -> ResultadoAutenticacion:
        ahora = self._reloj()
        with self._candado:
            fallos, desde = self._fallos.get(ip, (0, ahora))
            if fallos and ahora - desde >= self._segundos_bloqueo:
                fallos = 0
                self._fallos.pop(ip, None)
            if fallos >= self._max_fallos:
                return ResultadoAutenticacion(bloqueado=True, segundos_bloqueo=int(self._segundos_bloqueo - (ahora - desde)) + 1)
        if not cabecera or not cabecera.startswith("Basic "):
            return ResultadoAutenticacion()
        huella = hashlib.sha256(cabecera.encode("utf-8")).digest()
        with self._candado:
            recordada = self._validadas.get(huella)
            if recordada and ahora - recordada[1] < self._segundos_cache and recordada[0] in self._usuarios:
                return ResultadoAutenticacion(usuario=recordada[0])
        usuario = self._comprobar(cabecera)
        with self._candado:
            if usuario is None:
                fallos, desde = self._fallos.get(ip, (0, ahora))
                self._fallos[ip] = (fallos + 1, desde if fallos else ahora)
                log.warning("Intento de acceso fallido desde %s (%d seguidos).", ip, fallos + 1)
                return ResultadoAutenticacion()
            self._fallos.pop(ip, None)
            self._validadas[huella] = (usuario, ahora)
            if len(self._validadas) > 200:
                self._validadas.clear()
        return ResultadoAutenticacion(usuario=usuario)

    def _comprobar(self, cabecera: str) -> str | None:
        try:
            decodificado = base64.b64decode(cabecera[6:].strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        nombre, separador, clave = decodificado.partition(":")
        codificado = self._usuarios.get(nombre)
        if not separador or codificado is None:
            # Se calcula igual un hash para que la respuesta tarde lo mismo exista o no el usuario.
            verificar_hash(clave, _hash_senuelo())
            return None
        return nombre if verificar_hash(clave, codificado) else None


_senuelo: str | None = None


def _hash_senuelo() -> str:
    global _senuelo
    if _senuelo is None:
        _senuelo = generar_hash(secrets.token_urlsafe(16))
    return _senuelo


def cabeceras_seguridad(tipo_contenido: str) -> dict[str, str]:
    """Cabeceras para toda respuesta; la política de contenido solo aplica a las páginas HTML."""
    cabeceras = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Frame-Options": "SAMEORIGIN",
    }
    if tipo_contenido.startswith("text/html"):
        cabeceras["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; object-src 'self'; "
            "base-uri 'self'; form-action 'self'"
        )
    return cabeceras
