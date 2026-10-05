"""Política de cortesía hacia el portal de la Rama Judicial.

La forma de "no tumbar la página" y la de "no ser bloqueado" son la misma: comportarse
como un cliente respetuoso e identificable. Estas piezas se componen en el cliente HTTP:

* `LimitadorTasa`: cubeta de fichas; limita solicitudes por minuto con una ráfaga pequeña.
* `PoliticaReintentos`: retroceso exponencial con fluctuación aleatoria y respeto de `Retry-After`.
* `Cortacircuito`: tras N fallos seguidos deja de consultar durante un tiempo.
* `PresupuestoDiario`: tope duro de solicitudes por día (persistido).
* `VentanaHoraria`: opcionalmente restringe la vigilancia a ciertas horas.

Todas reciben el reloj y la función de espera por inyección para poder probarlas sin dormir.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from ..dominio.errores import PresupuestoAgotado
from ..dominio.puertos import ContadorSolicitudes

Reloj = Callable[[], float]
Dormir = Callable[[float], None]


class LimitadorTasa:
    """Cubeta de fichas: `solicitudes_por_minuto` sostenidas, con hasta `rafaga` inmediatas."""

    def __init__(
        self,
        solicitudes_por_minuto: float,
        rafaga: int = 1,
        reloj: Reloj = time.monotonic,
        dormir: Dormir = time.sleep,
    ) -> None:
        if solicitudes_por_minuto <= 0:
            raise ValueError("solicitudes_por_minuto debe ser mayor que cero")
        self._tasa = solicitudes_por_minuto / 60.0
        self._capacidad = max(1, int(rafaga))
        self._fichas = float(self._capacidad)
        self._reloj = reloj
        self._dormir = dormir
        self._ultimo = reloj()
        self._candado = threading.Lock()
        self.esperas_totales = 0.0

    @property
    def intervalo_segundos(self) -> float:
        return 1.0 / self._tasa

    def _rellenar(self) -> None:
        ahora = self._reloj()
        transcurrido = max(0.0, ahora - self._ultimo)
        self._ultimo = ahora
        self._fichas = min(float(self._capacidad), self._fichas + transcurrido * self._tasa)

    def esperar_turno(self) -> float:
        """Bloquea hasta que haya una ficha disponible. Devuelve los segundos esperados."""
        with self._candado:
            self._rellenar()
            if self._fichas >= 1.0:
                self._fichas -= 1.0
                return 0.0
            faltante = (1.0 - self._fichas) / self._tasa
            self._dormir(faltante)
            self._rellenar()
            self._fichas = max(0.0, self._fichas - 1.0)
            self.esperas_totales += faltante
            return faltante


def interpretar_retry_after(valor: str | None, ahora: datetime | None = None) -> float | None:
    """Convierte la cabecera Retry-After (segundos o fecha HTTP) en segundos de espera."""
    if not valor:
        return None
    valor = valor.strip()
    if valor.isdigit():
        return float(valor)
    try:
        fecha = parsedate_to_datetime(valor)
    except (TypeError, ValueError, IndexError):
        return None
    if fecha is None:
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    referencia = ahora or datetime.now(timezone.utc)
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=timezone.utc)
    return max(0.0, (fecha - referencia).total_seconds())


class PoliticaReintentos:
    """Retroceso exponencial con fluctuación ("equal jitter") y respeto de Retry-After."""

    CODIGOS_REINTENTABLES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        intentos_max: int = 4,
        espera_base: float = 2.0,
        factor: float = 2.0,
        espera_maxima: float = 60.0,
        retry_after_maximo: float = 300.0,
        aleatorio: Callable[[], float] = random.random,
    ) -> None:
        if intentos_max < 1:
            raise ValueError("intentos_max debe ser al menos 1")
        self.intentos_max = intentos_max
        self.espera_base = espera_base
        self.factor = factor
        self.espera_maxima = espera_maxima
        self.retry_after_maximo = retry_after_maximo
        self._aleatorio = aleatorio

    def es_reintentable(self, codigo: int | None) -> bool:
        """`None` representa un error de transporte (tiempo de espera, conexión): siempre reintentable."""
        return codigo is None or codigo in self.CODIGOS_REINTENTABLES

    def debe_reintentar(self, intento: int, codigo: int | None, retry_after: float | None = None) -> bool:
        if intento >= self.intentos_max:
            return False
        if not self.es_reintentable(codigo):
            return False
        if retry_after is not None and retry_after > self.retry_after_maximo:
            return False
        return True

    def calcular_espera(self, intento: int, retry_after: float | None = None) -> float:
        """Segundos a esperar antes del siguiente intento (`intento` comienza en 1)."""
        if retry_after is not None:
            return max(0.0, retry_after)
        exponencial = min(self.espera_base * (self.factor ** max(0, intento - 1)), self.espera_maxima)
        return exponencial * (0.5 + 0.5 * self._aleatorio())


class Cortacircuito:
    """Cortacircuito clásico: CERRADO -> ABIERTO (tras N fallos) -> SEMIABIERTO (una sonda) -> CERRADO."""

    CERRADO = "CERRADO"
    ABIERTO = "ABIERTO"
    SEMIABIERTO = "SEMIABIERTO"

    def __init__(self, umbral_fallos: int = 5, segundos_abierto: float = 300.0, reloj: Reloj = time.monotonic) -> None:
        if umbral_fallos < 1:
            raise ValueError("umbral_fallos debe ser al menos 1")
        self.umbral_fallos = umbral_fallos
        self.segundos_abierto = segundos_abierto
        self._reloj = reloj
        self._estado = self.CERRADO
        self._fallos = 0
        self._abierto_en = 0.0
        self._sonda_en_curso = False
        self._candado = threading.Lock()

    @property
    def estado(self) -> str:
        with self._candado:
            self._actualizar()
            return self._estado

    @property
    def fallos_consecutivos(self) -> int:
        return self._fallos

    def _actualizar(self) -> None:
        if self._estado == self.ABIERTO and self._reloj() - self._abierto_en >= self.segundos_abierto:
            self._estado = self.SEMIABIERTO
            self._sonda_en_curso = False

    def permitir(self) -> bool:
        with self._candado:
            self._actualizar()
            if self._estado == self.CERRADO:
                return True
            if self._estado == self.SEMIABIERTO and not self._sonda_en_curso:
                self._sonda_en_curso = True
                return True
            return False

    def segundos_restantes(self) -> float:
        with self._candado:
            self._actualizar()
            if self._estado != self.ABIERTO:
                return 0.0
            return max(0.0, self.segundos_abierto - (self._reloj() - self._abierto_en))

    def registrar_exito(self) -> None:
        with self._candado:
            self._estado = self.CERRADO
            self._fallos = 0
            self._sonda_en_curso = False

    def registrar_fallo(self) -> None:
        with self._candado:
            self._fallos += 1
            if self._estado == self.SEMIABIERTO or self._fallos >= self.umbral_fallos:
                self._estado = self.ABIERTO
                self._abierto_en = self._reloj()
                self._sonda_en_curso = False


class ContadorMemoria:
    """Contador de solicitudes en memoria (útil para consultas puntuales y pruebas)."""

    def __init__(self) -> None:
        self._valores: dict[date, int] = {}

    def obtener_contador(self, fecha: date) -> int:
        return self._valores.get(fecha, 0)

    def incrementar_contador(self, fecha: date, cantidad: int = 1) -> int:
        self._valores[fecha] = self._valores.get(fecha, 0) + cantidad
        return self._valores[fecha]


class PresupuestoDiario:
    """Tope duro de solicitudes por día natural, respaldado por un contador persistente."""

    def __init__(self, maximo: int, contador: ContadorSolicitudes, hoy: Callable[[], date] = date.today) -> None:
        if maximo < 1:
            raise ValueError("el presupuesto diario debe ser al menos 1")
        self.maximo = maximo
        self._contador = contador
        self._hoy = hoy

    def usado(self) -> int:
        return self._contador.obtener_contador(self._hoy())

    def restante(self) -> int:
        return max(0, self.maximo - self.usado())

    def consumir(self) -> int:
        fecha = self._hoy()
        usado = self._contador.obtener_contador(fecha)
        if usado >= self.maximo:
            raise PresupuestoAgotado(
                f"Se alcanzó el presupuesto diario de {self.maximo} solicitudes ({fecha.isoformat()})."
            )
        return self._contador.incrementar_contador(fecha, 1)


class VentanaHoraria:
    """Horas del día en las que se permite consultar: [hora_inicio, hora_fin).

    `hora_fin = 24` significa hasta medianoche. Si `hora_inicio > hora_fin` la ventana
    cruza la medianoche (por ejemplo 20 -> 6). (0, 24) equivale a sin restricción.
    """

    def __init__(self, hora_inicio: int = 0, hora_fin: int = 24) -> None:
        if not 0 <= hora_inicio <= 23 or not 1 <= hora_fin <= 24:
            raise ValueError("hora_inicio debe estar en 0..23 y hora_fin en 1..24")
        self.hora_inicio = hora_inicio
        self.hora_fin = hora_fin

    @property
    def sin_restriccion(self) -> bool:
        return self.hora_inicio == 0 and self.hora_fin == 24

    def permite(self, momento: datetime) -> bool:
        if self.sin_restriccion:
            return True
        hora = momento.hour
        if self.hora_inicio < self.hora_fin:
            return self.hora_inicio <= hora < self.hora_fin
        return hora >= self.hora_inicio or hora < (self.hora_fin % 24)

    def segundos_hasta_apertura(self, momento: datetime) -> float:
        if self.permite(momento):
            return 0.0
        apertura = momento.replace(hour=self.hora_inicio, minute=0, second=0, microsecond=0)
        if apertura <= momento:
            apertura += timedelta(days=1)
        return (apertura - momento).total_seconds()
