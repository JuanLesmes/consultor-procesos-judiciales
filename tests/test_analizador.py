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
from consultor_procesos.dominio.errores import ErrorFuente, RespuestaInesperada


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


def test_busqueda_sin_clave_procesos_solo_se_acepta_con_cero_resultados():
    assert analizar_respuesta_busqueda({"tipoConsulta": "NumeroRadicacion", "paginacion": {"cantidadRegistros": 0}}) == []
    with pytest.raises(RespuestaInesperada, match="'procesos'"):
        analizar_respuesta_busqueda({"tipoConsulta": "NumeroRadicacion"})
    renombrada = dict_busqueda([dict_proceso()])
    renombrada["listaProcesos"] = renombrada.pop("procesos")
    with pytest.raises(RespuestaInesperada):
        analizar_respuesta_busqueda(renombrada)


@pytest.mark.parametrize("clave", ["idProceso", "llaveProceso", "fechaUltimaActuacion", "despacho", "esPrivado"])
def test_busqueda_con_campo_renombrado_falla_en_voz_alta(clave):
    proceso = dict_proceso()
    proceso[clave + "Nuevo"] = proceso.pop(clave)
    with pytest.raises(RespuestaInesperada, match=clave):
        analizar_respuesta_busqueda(dict_busqueda([proceso]))


def test_busqueda_con_id_de_proceso_invalido():
    with pytest.raises(RespuestaInesperada, match="idProceso"):
        analizar_respuesta_busqueda(dict_busqueda([dict_proceso(id_proceso=0)]))


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


def test_pagina_de_actuaciones_vacia():
    pagina = analizar_pagina_actuaciones(dict_pagina_actuaciones([], total_paginas=0))
    assert pagina.actuaciones == () and pagina.total_paginas == 1 and pagina.pagina == 1 and not pagina.hay_mas
    assert analizar_pagina_actuaciones({"actuaciones": None, "paginacion": {"cantidadPaginas": 0, "pagina": 1}}).actuaciones == ()


@pytest.mark.parametrize(
    "datos",
    [
        {"actuaciones": []},
        {"paginacion": {"cantidadPaginas": 1, "pagina": 1}},
        {"actuaciones": [], "paginacion": None},
        {"actuaciones": [], "paginacion": {"paginas": 1}},
    ],
)
def test_pagina_de_actuaciones_sin_lista_o_paginacion_falla(datos):
    with pytest.raises(RespuestaInesperada):
        analizar_pagina_actuaciones(datos)


@pytest.mark.parametrize("clave", ["idRegActuacion", "consActuacion", "fechaActuacion", "actuacion", "anotacion", "conDocumentos"])
def test_actuacion_con_campo_renombrado_falla_en_voz_alta(clave):
    actuacion = dict_actuacion(1, 1)
    actuacion[clave + "V2"] = actuacion.pop(clave)
    with pytest.raises(RespuestaInesperada, match=clave):
        analizar_pagina_actuaciones(dict_pagina_actuaciones([actuacion]))


def test_actuacion_sin_identificador_valido_falla():
    actuacion = dict_actuacion(1, 1)
    actuacion["idRegActuacion"] = None
    with pytest.raises(RespuestaInesperada, match="idRegActuacion"):
        analizar_pagina_actuaciones(dict_pagina_actuaciones([actuacion]))


def test_detalle_con_campo_renombrado_falla():
    detalle = dict_detalle()
    detalle["codigoDespacho"] = detalle.pop("codDespachoCompleto")
    with pytest.raises(RespuestaInesperada, match="codDespachoCompleto"):
        analizar_detalle(detalle, id_proceso=ID_PROCESO)


def test_respuesta_inesperada_es_un_error_de_la_fuente():
    assert issubclass(RespuestaInesperada, ErrorFuente)


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
