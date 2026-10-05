from datetime import date, datetime

import pytest

from apoyo import DESPACHO, ID_PROCESO, RADICADO, dict_actuacion, dict_busqueda, dict_detalle, dict_pagina_actuaciones, dict_proceso
from consultor_procesos.adaptadores.cpnu.analizador import (
    analizar_detalle,
    analizar_fecha,
    analizar_fecha_hora,
    analizar_pagina_actuaciones,
    analizar_respuesta_busqueda,
)
from consultor_procesos.dominio.errores import ErrorFuente


def test_busqueda_con_resultado():
    procesos = analizar_respuesta_busqueda(dict_busqueda([dict_proceso()]))
    assert len(procesos) == 1
    proceso = procesos[0]
    assert proceso.id_proceso == ID_PROCESO
    assert proceso.radicado == RADICADO
    assert proceso.despacho == DESPACHO, "se recortan los espacios finales que devuelve la API"
    assert proceso.fecha_ultima_actuacion == date(2024, 5, 10)
    assert proceso.fecha_proceso == date(2024, 1, 15)
    assert proceso.es_privado is False
    assert "PERSONA DE PRUEBA" in proceso.sujetos


def test_busqueda_vacia():
    assert analizar_respuesta_busqueda(dict_busqueda([])) == []


def test_busqueda_sin_clave_procesos_se_tolera():
    assert analizar_respuesta_busqueda({"tipoConsulta": "NumeroRadicacion"}) == []


def test_busqueda_con_forma_inesperada():
    with pytest.raises(ErrorFuente):
        analizar_respuesta_busqueda([1, 2, 3])
    with pytest.raises(ErrorFuente):
        analizar_respuesta_busqueda({"procesos": "no-es-lista"})


def test_pagina_de_actuaciones():
    datos = dict_pagina_actuaciones(
        [
            dict_actuacion(1042815842, 11, actuacion="Envío  comunicaciones", anotacion="SE COMUNICA A LAS PARTES"),
            dict_actuacion(1042815352, 8, fecha="2024-04-24T00:00:00", anotacion="AUTO ORDENA REQUERIR", con_documentos=True),
        ],
        total=11,
    )
    pagina = analizar_pagina_actuaciones(datos, id_proceso=ID_PROCESO)
    assert pagina.total_registros == 11 and pagina.total_paginas == 1 and not pagina.hay_mas
    assert pagina.registros_por_pagina == 40
    primera, segunda = pagina.actuaciones
    assert primera.id_registro == 1042815842 and primera.consecutivo == 11
    assert primera.actuacion == "Envío  comunicaciones"
    assert primera.id_proceso == ID_PROCESO
    assert segunda.fecha_actuacion == date(2024, 4, 24) and segunda.con_documentos is True
    assert segunda.fecha_inicial is None


def test_pagina_de_actuaciones_sin_paginacion_ni_lista():
    pagina = analizar_pagina_actuaciones({"actuaciones": None})
    assert pagina.actuaciones == () and pagina.total_paginas == 1 and pagina.pagina == 1


def test_pagina_de_actuaciones_multiple():
    datos = dict_pagina_actuaciones([dict_actuacion(1, 41)], pagina=1, total_paginas=2, total=41)
    assert analizar_pagina_actuaciones(datos).hay_mas


def test_detalle():
    detalle = analizar_detalle(dict_detalle(), id_proceso=ID_PROCESO)
    assert detalle.id_proceso == ID_PROCESO, "el id del detalle es el de la búsqueda, no idRegProceso"
    assert detalle.ponente == "PONENTE DE PRUEBA"
    assert detalle.tipo_proceso == "Ejecutivo" and detalle.clase_proceso == "Ejecutivo Singular"
    assert detalle.ultima_actualizacion == datetime(2026, 9, 1, 19, 20, 40, 777000)
    assert detalle.contenido_radicacion == ""


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("2020-04-20T00:00:00", date(2020, 4, 20)),
        ("2026-09-02T01:30:35.487", date(2026, 9, 2)),
        (None, None),
        ("", None),
        ("no es fecha", None),
        (12345, None),
    ],
)
def test_analizar_fecha(entrada, esperado):
    assert analizar_fecha(entrada) == esperado


def test_analizar_fecha_hora_conserva_hora():
    assert analizar_fecha_hora("2026-09-02T01:30:35.487") == datetime(2026, 9, 2, 1, 30, 35, 487000)
