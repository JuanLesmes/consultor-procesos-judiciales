from datetime import date, datetime, timezone

import pytest

from apoyo import RelojFalso
from consultor_procesos.dominio.errores import PresupuestoAgotado
from consultor_procesos.infraestructura.cortesia import (
    ContadorMemoria,
    Cortacircuito,
    LimitadorTasa,
    PoliticaReintentos,
    PresupuestoDiario,
    VentanaHoraria,
    interpretar_retry_after,
)


class TestLimitadorTasa:
    def test_la_rafaga_no_espera(self, reloj: RelojFalso):
        limitador = LimitadorTasa(60, rafaga=2, reloj=reloj, dormir=reloj.dormir)
        assert limitador.esperar_turno() == 0.0
        assert limitador.esperar_turno() == 0.0
        assert reloj.esperas == []

    def test_espera_el_intervalo_al_agotar_las_fichas(self, reloj: RelojFalso):
        limitador = LimitadorTasa(12, rafaga=1, reloj=reloj, dormir=reloj.dormir)  # una cada 5 s
        assert limitador.esperar_turno() == 0.0
        esperado = limitador.esperar_turno()
        assert esperado == pytest.approx(5.0)
        assert reloj.esperas == [pytest.approx(5.0)]
        assert limitador.esperas_totales == pytest.approx(5.0)

    def test_rellena_con_el_paso_del_tiempo(self, reloj: RelojFalso):
        limitador = LimitadorTasa(12, rafaga=1, reloj=reloj, dormir=reloj.dormir)
        limitador.esperar_turno()
        reloj.avanzar(10)
        assert limitador.esperar_turno() == 0.0

    def test_rechaza_tasa_no_positiva(self):
        with pytest.raises(ValueError):
            LimitadorTasa(0)


class TestRetryAfter:
    def test_segundos(self):
        assert interpretar_retry_after("120") == 120.0

    def test_fecha_http(self):
        ahora = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        assert interpretar_retry_after("Wed, 02 Sep 2026 12:00:30 GMT", ahora) == pytest.approx(30.0)

    def test_fecha_pasada_es_cero(self):
        ahora = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        assert interpretar_retry_after("Wed, 02 Sep 2026 11:00:00 GMT", ahora) == 0.0

    def test_valores_invalidos(self):
        assert interpretar_retry_after(None) is None
        assert interpretar_retry_after("") is None
        assert interpretar_retry_after("basura") is None


class TestPoliticaReintentos:
    def test_codigos_reintentables(self):
        politica = PoliticaReintentos()
        assert politica.es_reintentable(429) and politica.es_reintentable(503) and politica.es_reintentable(None)
        assert not politica.es_reintentable(404) and not politica.es_reintentable(400)

    def test_espera_exponencial_con_tope(self):
        politica = PoliticaReintentos(espera_base=2.0, factor=2.0, espera_maxima=60.0, aleatorio=lambda: 1.0)
        assert [politica.calcular_espera(i) for i in (1, 2, 3)] == [2.0, 4.0, 8.0]
        assert politica.calcular_espera(10) == 60.0

    def test_la_fluctuacion_minima_es_la_mitad(self):
        politica = PoliticaReintentos(espera_base=2.0, aleatorio=lambda: 0.0)
        assert politica.calcular_espera(1) == 1.0

    def test_retry_after_prevalece(self):
        assert PoliticaReintentos().calcular_espera(1, retry_after=7.0) == 7.0

    def test_debe_reintentar(self):
        politica = PoliticaReintentos(intentos_max=4, retry_after_maximo=300)
        assert politica.debe_reintentar(1, 503)
        assert politica.debe_reintentar(3, None)
        assert not politica.debe_reintentar(4, 503), "se agotaron los intentos"
        assert not politica.debe_reintentar(1, 404), "error definitivo"
        assert not politica.debe_reintentar(1, 429, retry_after=1000), "el servidor pide esperar demasiado"

    def test_rechaza_intentos_invalidos(self):
        with pytest.raises(ValueError):
            PoliticaReintentos(intentos_max=0)


class TestCortacircuito:
    def test_se_abre_tras_el_umbral(self, reloj: RelojFalso):
        circuito = Cortacircuito(umbral_fallos=3, segundos_abierto=60, reloj=reloj)
        for _ in range(2):
            circuito.registrar_fallo()
        assert circuito.permitir() and circuito.estado == Cortacircuito.CERRADO
        circuito.registrar_fallo()
        assert not circuito.permitir()
        assert circuito.estado == Cortacircuito.ABIERTO
        assert circuito.segundos_restantes() == pytest.approx(60)

    def test_semiabierto_permite_una_sola_sonda_y_el_exito_cierra(self, reloj: RelojFalso):
        circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
        circuito.registrar_fallo()
        reloj.avanzar(60)
        assert circuito.estado == Cortacircuito.SEMIABIERTO
        assert circuito.permitir(), "la primera sonda pasa"
        assert not circuito.permitir(), "la segunda espera el resultado de la sonda"
        circuito.registrar_exito()
        assert circuito.estado == Cortacircuito.CERRADO and circuito.permitir()

    def test_fallo_en_la_sonda_reabre(self, reloj: RelojFalso):
        circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
        circuito.registrar_fallo()
        reloj.avanzar(60)
        assert circuito.permitir()
        circuito.registrar_fallo()
        assert circuito.estado == Cortacircuito.ABIERTO
        assert circuito.segundos_restantes() == pytest.approx(60)

    def test_el_exito_reinicia_el_contador(self, reloj: RelojFalso):
        circuito = Cortacircuito(umbral_fallos=3, reloj=reloj)
        circuito.registrar_fallo()
        circuito.registrar_fallo()
        circuito.registrar_exito()
        circuito.registrar_fallo()
        circuito.registrar_fallo()
        assert circuito.estado == Cortacircuito.CERRADO


class TestPresupuestoDiario:
    def test_consume_hasta_el_maximo_y_luego_falla(self):
        from datetime import date

        presupuesto = PresupuestoDiario(2, ContadorMemoria(), hoy=lambda: date(2026, 9, 2))
        assert presupuesto.consumir() == 1
        assert presupuesto.consumir() == 2
        assert presupuesto.restante() == 0
        with pytest.raises(PresupuestoAgotado):
            presupuesto.consumir()

    def test_se_reinicia_con_el_dia(self):
        from datetime import date

        dia = {"hoy": date(2026, 9, 2)}
        presupuesto = PresupuestoDiario(1, ContadorMemoria(), hoy=lambda: dia["hoy"])
        presupuesto.consumir()
        dia["hoy"] = date(2026, 9, 3)
        assert presupuesto.restante() == 1
        presupuesto.consumir()


class TestVentanaHoraria:
    def test_sin_restriccion(self):
        ventana = VentanaHoraria()
        assert ventana.sin_restriccion and ventana.permite(datetime(2026, 9, 2, 3))

    def test_rango_diurno(self):
        ventana = VentanaHoraria(8, 18)
        assert ventana.permite(datetime(2026, 9, 2, 8))
        assert ventana.permite(datetime(2026, 9, 2, 17, 59))
        assert not ventana.permite(datetime(2026, 9, 2, 18))
        assert not ventana.permite(datetime(2026, 9, 2, 7, 59))

    def test_cruza_medianoche(self):
        ventana = VentanaHoraria(20, 6)
        assert ventana.permite(datetime(2026, 9, 2, 23))
        assert ventana.permite(datetime(2026, 9, 2, 2))
        assert not ventana.permite(datetime(2026, 9, 2, 12))

    def test_segundos_hasta_apertura(self):
        ventana = VentanaHoraria(8, 18)
        assert ventana.segundos_hasta_apertura(datetime(2026, 9, 2, 6, 30)) == 5400
        assert ventana.segundos_hasta_apertura(datetime(2026, 9, 2, 19, 0)) == 13 * 3600
        assert ventana.segundos_hasta_apertura(datetime(2026, 9, 2, 10, 0)) == 0

    def test_rechaza_horas_invalidas(self):
        with pytest.raises(ValueError):
            VentanaHoraria(24, 24)
        with pytest.raises(ValueError):
            VentanaHoraria(0, 0)


class TestCortacircuitoSinBloqueos:
    def test_sonda_sin_resolver_vence_y_permite_otra(self):
        reloj = RelojFalso()
        circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
        circuito.registrar_fallo()
        reloj.avanzar(60)
        assert circuito.permitir() is True
        assert circuito.permitir() is False, "una sola sonda a la vez"
        reloj.avanzar(59)
        assert circuito.permitir() is False
        reloj.avanzar(1)
        assert circuito.permitir() is True, "la sonda que nunca se resolvió vence"

    def test_liberar_sonda(self):
        reloj = RelojFalso()
        circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
        circuito.registrar_fallo()
        reloj.avanzar(60)
        assert circuito.permitir() and not circuito.permitir()
        circuito.liberar_sonda()
        assert circuito.permitir()
        assert circuito.estado == Cortacircuito.SEMIABIERTO


class TestPresupuestoAtomico:
    def test_hilos_concurrentes_no_se_pasan_del_tope(self):
        import threading

        contador = ContadorMemoria()
        presupuesto = PresupuestoDiario(50, contador, hoy=lambda: date(2026, 9, 2))
        consumidas: list[int] = []
        agotadas: list[int] = []

        def trabajar() -> None:
            for _ in range(20):
                try:
                    consumidas.append(presupuesto.consumir())
                except PresupuestoAgotado:
                    agotadas.append(1)

        hilos = [threading.Thread(target=trabajar) for _ in range(8)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        assert len(consumidas) == 50 and len(agotadas) == 110
        assert sorted(consumidas) == list(range(1, 51))
        assert contador.obtener_contador(date(2026, 9, 2)) == 50
