"""Horario de verificación (horas fijas en días hábiles) y su uso por el planificador."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from apoyo import RelojCalendario
from consultor_procesos.aplicacion.horario import Horario, parsear_dia, parsear_hora
from consultor_procesos.aplicacion.planificador import Planificador

# Miércoles 2 de septiembre de 2026.
MIERCOLES = date(2026, 9, 2)
HORARIO = Horario.desde_texto(["07:00", "10:00", "13:00", "17:00"], hasta="18:00")


class TestParseo:
    def test_horas_en_varios_formatos(self):
        assert parsear_hora("07:00") == time(7, 0)
        assert parsear_hora("7") == time(7, 0)
        assert parsear_hora(17) == time(17, 0)
        assert parsear_hora("17:30") == time(17, 30)
        assert parsear_hora(time(9, 15)) == time(9, 15)

    @pytest.mark.parametrize("texto", ["25:00", "7:60", "mañana", "", True])
    def test_horas_invalidas(self, texto):
        with pytest.raises(ValueError):
            parsear_hora(texto)

    def test_dias(self):
        assert [parsear_dia(d) for d in ("lun", "Martes", "MIÉ", "jue", "viernes", "sab", "dom")] == [0, 1, 2, 3, 4, 5, 6]
        assert parsear_dia(4) == 4
        with pytest.raises(ValueError):
            parsear_dia("hoy")
        with pytest.raises(ValueError):
            parsear_dia(7)

    def test_desde_texto_ordena_y_valida(self):
        horario = Horario.desde_texto(["17:00", "07:00", "07:00"], dias=["vie", "lun"], hasta="18:00", festivos=["2026-12-25"])
        assert horario.horas == (time(7, 0), time(17, 0))
        assert sorted(horario.dias) == [0, 4]
        assert horario.festivos == frozenset({date(2026, 12, 25)})
        assert horario.a_dict() == {
            "horas": ["07:00", "17:00"],
            "dias": ["lun", "vie"],
            "hasta": "18:00",
            "festivos": ["2026-12-25"],
            "descripcion": "los lunes, viernes a las 07:00 y 17:00",
        }

    def test_horario_invalido(self):
        with pytest.raises(ValueError):
            Horario.desde_texto([])
        with pytest.raises(ValueError):
            Horario.desde_texto(["07:00"], dias=[])
        with pytest.raises(ValueError):
            Horario.desde_texto(["07:00", "17:00"], hasta="16:00")

    def test_descripcion_y_fin_de_jornada_predeterminado(self):
        assert HORARIO.descripcion() == "de lunes a viernes a las 07:00, 10:00, 13:00 y 17:00"
        sin_hasta = Horario.desde_texto(["09:00", "16:30"])
        assert sin_hasta.fin_jornada == time(17, 30)
        assert Horario.desde_texto(["23:30"]).fin_jornada == time(23, 59, 59)
        assert Horario.desde_texto(["08:00"], dias=NOMBRES_TODOS).descripcion() == "todos los días a las 08:00"


NOMBRES_TODOS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]


class TestCalculo:
    def test_proxima_dentro_del_dia(self):
        assert HORARIO.proxima(datetime(2026, 9, 2, 9, 59)) == datetime(2026, 9, 2, 10, 0)
        assert HORARIO.proxima(datetime(2026, 9, 2, 10, 0)) == datetime(2026, 9, 2, 13, 0), "la hora exacta ya pasó"

    def test_proxima_salta_la_noche_y_el_fin_de_semana(self):
        assert HORARIO.proxima(datetime(2026, 9, 2, 17, 30)) == datetime(2026, 9, 3, 7, 0)
        assert HORARIO.proxima(datetime(2026, 9, 4, 17, 30)) == datetime(2026, 9, 7, 7, 0), "viernes tarde -> lunes"
        assert HORARIO.proxima(datetime(2026, 9, 5, 9, 0)) == datetime(2026, 9, 7, 7, 0), "sábado -> lunes"

    def test_proxima_respeta_festivos(self):
        horario = Horario.desde_texto(["07:00"], festivos=["2026-09-03"])
        assert horario.proxima(datetime(2026, 9, 2, 8, 0)) == datetime(2026, 9, 4, 7, 0)

    def test_ultima(self):
        assert HORARIO.ultima(datetime(2026, 9, 2, 12, 0)) == datetime(2026, 9, 2, 10, 0)
        assert HORARIO.ultima(datetime(2026, 9, 2, 10, 0)) == datetime(2026, 9, 2, 10, 0)
        assert HORARIO.ultima(datetime(2026, 9, 2, 6, 0)) == datetime(2026, 9, 1, 17, 0)
        assert HORARIO.ultima(datetime(2026, 9, 6, 12, 0)) == datetime(2026, 9, 4, 17, 0), "domingo -> viernes"

    def test_en_jornada(self):
        assert HORARIO.en_jornada(datetime(2026, 9, 2, 7, 0))
        assert HORARIO.en_jornada(datetime(2026, 9, 2, 17, 59))
        assert not HORARIO.en_jornada(datetime(2026, 9, 2, 18, 0)), "a las 18:00 ya no"
        assert not HORARIO.en_jornada(datetime(2026, 9, 2, 6, 59))
        assert not HORARIO.en_jornada(datetime(2026, 9, 5, 10, 0)), "sábado"

    def test_pendiente(self):
        mediodia = datetime(2026, 9, 2, 12, 0)
        assert HORARIO.pendiente(None, mediodia), "nunca se ha ejecutado y ya pasaron las 10:00"
        assert HORARIO.pendiente(datetime(2026, 9, 2, 7, 3), mediodia), "la de las 10:00 no se hizo"
        assert not HORARIO.pendiente(datetime(2026, 9, 2, 10, 2), mediodia), "la de las 10:00 sí se hizo"
        assert not HORARIO.pendiente(None, datetime(2026, 9, 2, 20, 0)), "de noche no se recupera nada"
        assert not HORARIO.pendiente(None, datetime(2026, 9, 5, 12, 0)), "sábado tampoco"
        assert not HORARIO.pendiente(None, datetime(2026, 9, 2, 6, 30)), "antes de la primera hora no hay nada pendiente"


class TestPlanificadorPorHorario:
    def hacer(self, reloj: RelojCalendario, ejecuciones: list, ultima=None, fluctuacion=0.0, aleatorio=lambda: 0.0):
        return Planificador(
            lambda: ejecuciones.append(reloj.ahora),
            horario=HORARIO,
            ahora=reloj,
            dormir=reloj.dormir,
            aleatorio=aleatorio,
            ultima_ejecucion=lambda: ultima,
            fluctuacion_segundos=fluctuacion,
        )

    def test_ejecuta_a_las_horas_programadas(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 6, 0))
        ejecuciones: list[datetime] = []
        planificador = self.hacer(reloj, ejecuciones)
        assert planificador.ejecutar(max_ciclos=5) == 5
        assert ejecuciones == [
            datetime(2026, 9, 2, 7, 0),
            datetime(2026, 9, 2, 10, 0),
            datetime(2026, 9, 2, 13, 0),
            datetime(2026, 9, 2, 17, 0),
            datetime(2026, 9, 3, 7, 0),
        ]
        assert reloj.esperas[0] == 3600.0 and reloj.esperas[3] == 4 * 3600.0
        assert reloj.esperas[4] == 14 * 3600.0, "de las 17:00 a las 07:00 del día siguiente"

    def test_al_arrancar_recupera_la_verificacion_pendiente(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 11, 30))
        ejecuciones: list[datetime] = []
        planificador = self.hacer(reloj, ejecuciones, ultima=datetime(2026, 9, 2, 7, 2))
        assert planificador.ejecutar(max_ciclos=2) == 2
        assert ejecuciones == [datetime(2026, 9, 2, 11, 30), datetime(2026, 9, 2, 13, 0)]
        assert reloj.esperas == [5400.0]

    def test_al_arrancar_no_repite_si_ya_se_hizo(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 11, 30))
        ejecuciones: list[datetime] = []
        planificador = self.hacer(reloj, ejecuciones, ultima=datetime(2026, 9, 2, 10, 4))
        assert planificador.ejecutar(max_ciclos=1) == 1
        assert ejecuciones == [datetime(2026, 9, 2, 13, 0)]

    def test_de_noche_espera_a_la_manana_siguiente(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 20, 0))
        ejecuciones: list[datetime] = []
        planificador = self.hacer(reloj, ejecuciones, ultima=None)
        assert planificador.ejecutar(max_ciclos=1) == 1
        assert ejecuciones == [datetime(2026, 9, 3, 7, 0)]
        assert planificador.proxima_ejecucion is None

    def test_fin_de_semana_espera_al_lunes(self):
        reloj = RelojCalendario(datetime(2026, 9, 5, 9, 0))
        ejecuciones: list[datetime] = []
        assert self.hacer(reloj, ejecuciones).ejecutar(max_ciclos=1) == 1
        assert ejecuciones == [datetime(2026, 9, 7, 7, 0)]

    def test_fluctuacion_retrasa_pero_no_adelanta(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 6, 0))
        ejecuciones: list[datetime] = []
        planificador = self.hacer(reloj, ejecuciones, fluctuacion=300, aleatorio=lambda: 0.5)
        assert planificador.ejecutar(max_ciclos=1) == 1
        assert ejecuciones == [datetime(2026, 9, 2, 7, 2, 30)]

    def test_si_despierta_fuera_de_la_jornada_reprograma(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 16, 0))
        ejecuciones: list[datetime] = []
        saltos = iter([3 * 3600.0])  # el equipo estuvo suspendido y "durmió" 3 h en vez de 1

        def dormir(segundos: float) -> None:
            reloj.dormir(next(saltos, segundos))

        planificador = Planificador(
            lambda: ejecuciones.append(reloj.ahora),
            horario=HORARIO,
            ahora=reloj,
            dormir=dormir,
            aleatorio=lambda: 0.0,
            ultima_ejecucion=lambda: datetime(2026, 9, 2, 13, 5),
        )
        assert planificador.ejecutar(max_ciclos=1) == 1
        assert ejecuciones == [datetime(2026, 9, 3, 7, 0)], "a las 19:00 no verifica; espera al día siguiente"

    def test_proxima_ejecucion_visible_mientras_espera(self):
        reloj = RelojCalendario(datetime(2026, 9, 2, 6, 0))
        vistas: list[datetime | None] = []
        planificador = Planificador(lambda: None, horario=HORARIO, ahora=reloj, dormir=lambda s: (vistas.append(planificador.proxima_ejecucion), reloj.dormir(s)))
        planificador.ejecutar(max_ciclos=1)
        assert vistas == [datetime(2026, 9, 2, 7, 0)]

    def test_descripcion(self):
        planificador = Planificador(lambda: None, horario=HORARIO)
        assert planificador.descripcion() == "de lunes a viernes a las 07:00, 10:00, 13:00 y 17:00 (hasta las 18:00)"
        assert Planificador(lambda: None, intervalo_segundos=600).descripcion() == "cada ~10 min"

    def test_sin_horario_ni_intervalo_es_error(self):
        with pytest.raises(ValueError):
            Planificador(lambda: None)
        with pytest.raises(ValueError):
            Planificador(lambda: None, horario=HORARIO, fluctuacion_segundos=-1)
