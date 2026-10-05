from datetime import date

import pytest

from apoyo import ID_PROCESO, RADICADO, RADICADO_2, hacer_actuacion, hacer_proceso
from consultor_procesos.dominio.errores import RadicadoInvalido
from consultor_procesos.dominio.reglas import (
    DetectorAutos,
    calcular_huella,
    calcular_novedades,
    fecha_ultima_actuacion,
    normalizar,
    seleccionar_procesos,
    validar_radicado,
)


class TestNormalizar:
    def test_quita_tildes_colapsa_espacios_y_pone_mayusculas(self):
        assert normalizar("  Fijación   estado ") == "FIJACION ESTADO"

    def test_vacio_y_none(self):
        assert normalizar(None) == ""
        assert normalizar("   ") == ""


class TestDetectorAutos:
    def test_detecta_en_el_tipo_de_actuacion(self):
        detector = DetectorAutos()
        assert detector.es_auto(hacer_actuacion(1, 1, actuacion="Auto admite demanda"))

    def test_detecta_en_la_anotacion_bajo_constancia_secretarial(self):
        # Patrón real: el despacho registra "Constancia secretarial" y describe el auto en la anotación.
        detector = DetectorAutos()
        actuacion = hacer_actuacion(1, 1, actuacion="Constancia secretarial", anotacion="AUTO ORDENA REQUERIR AL JUZGADO")
        assert detector.coincidencias(actuacion) == ("AUTO",)

    def test_ignora_palabras_que_solo_contienen_auto(self):
        detector = DetectorAutos()
        assert not detector.es_auto(hacer_actuacion(1, 1, anotacion="Embargo de vehículo AUTOMOTOR"))
        assert not detector.es_auto(hacer_actuacion(1, 1, anotacion="SE AUTORIZA RETIRO DE TÍTULOS"))

    def test_plural_guion_y_tildes(self):
        detector = DetectorAutos()
        assert detector.es_auto(hacer_actuacion(1, 1, anotacion="Pasan los AUTOS al despacho"))
        assert detector.es_auto(hacer_actuacion(1, 1, anotacion="AUTO-ADMISORIO notificado"))
        assert detector.es_auto(hacer_actuacion(1, 1, actuacion="Autó que resuelve recurso"))

    def test_palabras_clave_adicionales(self):
        detector = DetectorAutos(["auto", "sentencia"])
        actuacion = hacer_actuacion(1, 1, actuacion="Sentencia de primera instancia")
        assert detector.coincidencias(actuacion) == ("SENTENCIA",)
        assert detector.palabras_clave == ("AUTO", "SENTENCIA")

    def test_sin_palabras_clave_nunca_detecta(self):
        detector = DetectorAutos([])
        assert not detector.es_auto(hacer_actuacion(1, 1, actuacion="AUTO ADMITE"))

    def test_evaluar_devuelve_novedad_con_despacho(self):
        novedad = DetectorAutos().evaluar(hacer_actuacion(7, 3, actuacion="Auto fija fecha"), despacho="JUZGADO X")
        assert novedad.es_auto and novedad.despacho == "JUZGADO X" and novedad.radicado == RADICADO
        assert novedad.actuacion.id_registro == 7


class TestNovedades:
    def test_filtra_conocidas_y_ordena_ascendente(self):
        actuaciones = [hacer_actuacion(30, 3), hacer_actuacion(10, 1), hacer_actuacion(20, 2)]
        nuevas = calcular_novedades(actuaciones, ids_conocidos={10})
        assert [a.id_registro for a in nuevas] == [20, 30]

    def test_elimina_duplicados_de_entrada(self):
        actuaciones = [hacer_actuacion(10, 1), hacer_actuacion(10, 1)]
        assert len(calcular_novedades(actuaciones, set())) == 1


class TestSeleccionarProcesos:
    def test_un_solo_proceso(self):
        proceso = hacer_proceso()
        assert seleccionar_procesos([proceso], RADICADO) == [proceso]

    def test_conserva_varios_despachos_del_mismo_radicado_mas_reciente_primero(self):
        juzgado = hacer_proceso(id_proceso=1, fecha_ultima=date(2024, 1, 1))
        tribunal = hacer_proceso(id_proceso=2, fecha_ultima=date(2025, 1, 1), despacho="TRIBUNAL")
        assert seleccionar_procesos([juzgado, tribunal], RADICADO) == [tribunal, juzgado]

    def test_deduplica_por_id(self):
        proceso = hacer_proceso()
        assert len(seleccionar_procesos([proceso, proceso], RADICADO)) == 1

    def test_vacio(self):
        assert seleccionar_procesos([], RADICADO) == []

    def test_prefiere_coincidencia_exacta_de_radicado(self):
        otro = hacer_proceso(id_proceso=9, radicado=RADICADO_2)
        propio = hacer_proceso(id_proceso=1)
        assert seleccionar_procesos([otro, propio], RADICADO) == [propio]


class TestHuella:
    def test_es_estable_sin_importar_el_orden(self):
        a = hacer_proceso(id_proceso=1, fecha_ultima=date(2024, 1, 1))
        b = hacer_proceso(id_proceso=2, fecha_ultima=None)
        assert calcular_huella([a, b]) == calcular_huella([b, a]) == "1:2024-01-01;2:-"

    def test_cambia_cuando_cambia_la_fecha(self):
        antes = calcular_huella([hacer_proceso(fecha_ultima=date(2024, 1, 1))])
        despues = calcular_huella([hacer_proceso(fecha_ultima=date(2024, 1, 2))])
        assert antes != despues

    def test_fecha_ultima_actuacion_es_la_maxima(self):
        procesos = [hacer_proceso(id_proceso=1, fecha_ultima=date(2024, 1, 1)), hacer_proceso(id_proceso=2, fecha_ultima=date(2025, 6, 1))]
        assert fecha_ultima_actuacion(procesos) == date(2025, 6, 1)
        assert fecha_ultima_actuacion([hacer_proceso(fecha_ultima=None)]) is None


class TestValidarRadicado:
    @pytest.mark.parametrize(
        "entrada",
        [RADICADO, "11001 4003 001 2024 00123 45", "11001-4003001-2024-00123-45", " 11001400300120240012345 "],
    )
    def test_acepta_y_normaliza(self, entrada):
        assert validar_radicado(entrada) == RADICADO

    @pytest.mark.parametrize("entrada", ["123", "1100140030012024001234A", None, RADICADO + "1", ""])
    def test_rechaza_formatos_invalidos(self, entrada):
        with pytest.raises(RadicadoInvalido):
            validar_radicado(entrada)
