"""Bucle de vigilancia periódica: por horario (horas fijas en días hábiles) o por intervalo.

* Con `horario`, el ciclo corre a cada hora programada (más una pequeña fluctuación al azar,
  para no llegar todos los días al mismo segundo). Al arrancar, si dentro de la jornada ya
  pasó una hora programada que no llegó a ejecutarse (el programa estaba apagado), se ejecuta
  de inmediato; fuera de la jornada solo se espera a la siguiente.
* Con `intervalo_segundos`, el ciclo corre cada tanto tiempo (+/- `jitter_fraccion`) dentro de
  la ventana horaria opcional.

Los fallos de la fuente no detienen el bucle: se registran y se espera al siguiente ciclo.
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from ..dominio.errores import FuenteNoDisponible, PresupuestoAgotado
from ..infraestructura.cortesia import VentanaHoraria
from .horario import Horario

log = logging.getLogger(__name__)


class Planificador:
    def __init__(
        self,
        ciclo: Callable[[], object],
        intervalo_segundos: float | None = None,
        jitter_fraccion: float = 0.2,
        ventana: VentanaHoraria | None = None,
        ahora: Callable[[], datetime] = datetime.now,
        dormir: Callable[[float], None] | None = None,
        aleatorio: Callable[[], float] = random.random,
        horario: Horario | None = None,
        ultima_ejecucion: Callable[[], datetime | None] | None = None,
        fluctuacion_segundos: float = 0.0,
    ) -> None:
        if horario is None and (intervalo_segundos is None or intervalo_segundos <= 0):
            raise ValueError("intervalo_segundos debe ser mayor que cero (o indique un horario)")
        if not 0.0 <= jitter_fraccion < 1.0:
            raise ValueError("jitter_fraccion debe estar en [0, 1)")
        if fluctuacion_segundos < 0:
            raise ValueError("fluctuacion_segundos no puede ser negativa")
        self._ciclo = ciclo
        self._intervalo = intervalo_segundos or 0.0
        self._jitter = jitter_fraccion
        self._ventana = ventana
        self._ahora = ahora
        self._detener = threading.Event()
        self._dormir = dormir if dormir is not None else self._esperar_interrumpible
        self._aleatorio = aleatorio
        self.horario = horario
        self._ultima_ejecucion = ultima_ejecucion or (lambda: None)
        self._fluctuacion = fluctuacion_segundos
        self.ciclos_ejecutados = 0
        self.proxima_ejecucion: datetime | None = None

    def detener(self) -> None:
        """Pide parar. Con la espera predeterminada, el bucle despierta de inmediato."""
        self._detener.set()

    def _esperar_interrumpible(self, segundos: float) -> None:
        self._detener.wait(max(0.0, segundos))

    def descripcion(self) -> str:
        if self.horario is not None:
            return f"{self.horario.descripcion()} (hasta las {self.horario.fin_jornada:%H:%M})"
        texto = f"cada ~{self._intervalo / 60:g} min"
        if self._ventana is not None and not self._ventana.sin_restriccion:
            texto += f", entre las {self._ventana.hora_inicio:02d}:00 y las {self._ventana.hora_fin:02d}:00"
        return texto

    def calcular_espera(self) -> float:
        return self._intervalo * (1.0 + self._jitter * (2.0 * self._aleatorio() - 1.0))

    def ejecutar(self, max_ciclos: int | None = None) -> int:
        if self.horario is not None:
            return self._ejecutar_por_horario(max_ciclos)
        return self._ejecutar_por_intervalo(max_ciclos)

    def _correr_ciclo(self) -> None:
        self.proxima_ejecucion = None
        try:
            self._ciclo()
        except (FuenteNoDisponible, PresupuestoAgotado) as exc:
            log.error("Ciclo de vigilancia interrumpido: %s", exc)
        self.ciclos_ejecutados += 1

    def _quedan_ciclos(self, max_ciclos: int | None) -> bool:
        return not self._detener.is_set() and (max_ciclos is None or self.ciclos_ejecutados < max_ciclos)

    # --- por horario -----------------------------------------------------------------------

    def _ejecutar_por_horario(self, max_ciclos: int | None) -> int:
        horario = self.horario
        assert horario is not None
        primera = True
        while self._quedan_ciclos(max_ciclos):
            momento = self._ahora()
            if primera and horario.pendiente(self._ultima_ejecucion(), momento):
                programada = horario.ultima(momento)
                log.info(
                    "La verificación de las %s no se ejecutó (el programa estaba apagado); se ejecuta ahora.",
                    programada.strftime("%H:%M") if programada else "?",
                )
            else:
                objetivo = horario.proxima(momento) + timedelta(seconds=self._fluctuacion * self._aleatorio())
                self.proxima_ejecucion = objetivo
                log.info("Próxima verificación programada: %s.", objetivo.strftime("%Y-%m-%d %H:%M"))
                self._dormir((objetivo - momento).total_seconds())
                if self._detener.is_set():
                    break
                momento = self._ahora()
                if not horario.en_jornada(momento):
                    # Ocurre cuando el equipo estuvo suspendido y despertó de noche o en fin de semana.
                    log.info("Se despertó fuera de la jornada (%s); se reprograma.", momento.strftime("%Y-%m-%d %H:%M"))
                    primera = False
                    continue
            primera = False
            self._correr_ciclo()
        self.proxima_ejecucion = None
        return self.ciclos_ejecutados

    # --- por intervalo ---------------------------------------------------------------------

    def _ejecutar_por_intervalo(self, max_ciclos: int | None) -> int:
        while self._quedan_ciclos(max_ciclos):
            momento = self._ahora()
            if self._ventana is not None and not self._ventana.permite(momento):
                espera = self._ventana.segundos_hasta_apertura(momento)
                self.proxima_ejecucion = momento + timedelta(seconds=espera)
                log.info("Fuera de la ventana horaria; la vigilancia se reanuda en %.0f min.", espera / 60)
                self._dormir(espera)
                continue
            self._correr_ciclo()
            if max_ciclos is not None and self.ciclos_ejecutados >= max_ciclos:
                break
            espera = self.calcular_espera()
            self.proxima_ejecucion = self._ahora() + timedelta(seconds=espera)
            log.info("Próximo ciclo de vigilancia en %.0f min.", espera / 60)
            self._dormir(espera)
            self.proxima_ejecucion = None
        return self.ciclos_ejecutados
