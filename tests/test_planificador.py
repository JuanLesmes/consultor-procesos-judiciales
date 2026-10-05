from datetime import datetime

import pytest

from apoyo import RelojCalendario
from consultor_procesos.aplicacion.planificador import Planificador
from consultor_procesos.dominio.errores import FuenteNoDisponible
from consultor_procesos.infraestructura.cortesia import VentanaHoraria


def test_ejecuta_los_ciclos_con_fluctuacion_y_sin_espera_final():
    reloj = RelojCalendario(datetime(2026, 9, 2, 10, 0))
    ejecuciones = []
    planificador = Planificador(
        lambda: ejecuciones.append(reloj.ahora),
        intervalo_segundos=600,
        jitter_fraccion=0.2,
        ahora=reloj,
        dormir=reloj.dormir,
        aleatorio=lambda: 1.0,
    )
    assert planificador.ejecutar(max_ciclos=3) == 3
    assert len(ejecuciones) == 3
    assert reloj.esperas == [720.0, 720.0], "intervalo * 1.2; no se duerme tras el último ciclo"


def test_la_fluctuacion_negativa_acorta_el_intervalo():
    planificador = Planificador(lambda: None, intervalo_segundos=600, jitter_fraccion=0.2, aleatorio=lambda: 0.0)
    assert planificador.calcular_espera() == pytest.approx(480.0)


def test_espera_a_la_ventana_horaria():
    reloj = RelojCalendario(datetime(2026, 9, 2, 6, 0))
    ejecuciones = []
    planificador = Planificador(
        lambda: ejecuciones.append(reloj.ahora),
        intervalo_segundos=600,
        jitter_fraccion=0.0,
        ventana=VentanaHoraria(8, 18),
        ahora=reloj,
        dormir=reloj.dormir,
    )
    assert planificador.ejecutar(max_ciclos=1) == 1
    assert reloj.esperas == [7200.0]
    assert ejecuciones == [datetime(2026, 9, 2, 8, 0)]


def test_un_fallo_de_la_fuente_no_detiene_el_bucle():
    reloj = RelojCalendario(datetime(2026, 9, 2, 10, 0))
    intentos = {"n": 0}

    def ciclo():
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise FuenteNoDisponible("caída")

    planificador = Planificador(ciclo, intervalo_segundos=60, jitter_fraccion=0.0, ahora=reloj, dormir=reloj.dormir)
    assert planificador.ejecutar(max_ciclos=2) == 2
    assert intentos["n"] == 2


def test_detener_desde_el_ciclo():
    reloj = RelojCalendario(datetime(2026, 9, 2, 10, 0))
    planificador = Planificador(lambda: planificador.detener(), intervalo_segundos=60, ahora=reloj, dormir=reloj.dormir)
    assert planificador.ejecutar() == 1
    assert reloj.esperas == [pytest.approx(60.0, rel=0.25)], "duerme una vez antes de comprobar la señal de parada"


def test_parametros_invalidos():
    with pytest.raises(ValueError):
        Planificador(lambda: None, intervalo_segundos=0)
    with pytest.raises(ValueError):
        Planificador(lambda: None, intervalo_segundos=10, jitter_fraccion=1.0)


def test_detener_despierta_de_inmediato_con_la_espera_predeterminada():
    import threading
    import time

    planificador = Planificador(lambda: None, intervalo_segundos=3600, jitter_fraccion=0.0)
    hilo = threading.Thread(target=planificador.ejecutar, daemon=True)
    inicio = time.monotonic()
    hilo.start()
    time.sleep(0.05)
    planificador.detener()
    hilo.join(timeout=5)
    assert not hilo.is_alive(), "el bucle debe salir sin esperar la hora completa"
    assert time.monotonic() - inicio < 5
    assert planificador.ciclos_ejecutados == 1
