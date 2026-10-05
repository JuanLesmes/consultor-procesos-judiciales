"""Pruebas del cliente CPNU con respuestas HTTP simuladas (respx). No tocan la red."""

from __future__ import annotations

import httpx
import pytest
import respx

from apoyo import (
    ID_PROCESO,
    RADICADO,
    RelojFalso,
    dict_actuacion,
    dict_busqueda,
    dict_detalle,
    dict_pagina_actuaciones,
    dict_proceso,
)
from consultor_procesos.adaptadores.cpnu.cliente import URL_BASE_CPNU, ClienteCPNU
from consultor_procesos.dominio.errores import (
    CircuitoAbierto,
    ErrorFuente,
    FuenteNoDisponible,
    PresupuestoAgotado,
    RadicadoInvalido,
)
from consultor_procesos.infraestructura.cortesia import (
    ContadorMemoria,
    Cortacircuito,
    LimitadorTasa,
    PoliticaReintentos,
    PresupuestoDiario,
)

URL_BUSQUEDA = f"{URL_BASE_CPNU}/Procesos/Consulta/NumeroRadicacion"
URL_ACTUACIONES = f"{URL_BASE_CPNU}/Proceso/Actuaciones/{ID_PROCESO}"
URL_DETALLE = f"{URL_BASE_CPNU}/Proceso/Detalle/{ID_PROCESO}"
AGENTE = "PruebaUA/1.0 (contacto: pruebas@ejemplo.com)"


def hacer_cliente(reloj: RelojFalso, **kwargs) -> ClienteCPNU:
    limitador = kwargs.pop("limitador", LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir))
    reintentos = kwargs.pop("reintentos", PoliticaReintentos(intentos_max=3, espera_base=1.0, aleatorio=lambda: 1.0))
    cortacircuito = kwargs.pop("cortacircuito", Cortacircuito(umbral_fallos=5, segundos_abierto=60, reloj=reloj))
    return ClienteCPNU(
        agente_usuario=AGENTE,
        limitador=limitador,
        reintentos=reintentos,
        cortacircuito=cortacircuito,
        dormir=reloj.dormir,
        **kwargs,
    )


@respx.mock
def test_busqueda_exitosa_se_identifica_y_envia_los_parametros(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
    with hacer_cliente(reloj) as cliente:
        procesos = cliente.buscar_por_radicado(RADICADO)
        assert cliente.solicitudes_realizadas == 1
    assert len(procesos) == 1 and procesos[0].id_proceso == ID_PROCESO
    solicitud = ruta.calls.last.request
    assert solicitud.headers["User-Agent"] == AGENTE
    assert solicitud.headers["Accept"] == "application/json"
    assert solicitud.url.params["numero"] == RADICADO
    assert solicitud.url.params["SoloActivos"] == "false"
    assert solicitud.url.params["pagina"] == "1"


@respx.mock
def test_radicado_invalido_no_consulta_la_fuente(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, json=dict_busqueda()))
    with hacer_cliente(reloj) as cliente, pytest.raises(RadicadoInvalido):
        cliente.buscar_por_radicado("123")
    assert not ruta.called


@respx.mock
def test_error_definitivo_no_se_reintenta_ni_abre_el_circuito(reloj):
    cuerpo = {"StatusCode": 404, "Message": 'El parametro "NumeroRadicacion" ha de contener 23 digitos.'}
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(404, json=cuerpo))
    circuito = Cortacircuito(umbral_fallos=1, reloj=reloj)
    with hacer_cliente(reloj, cortacircuito=circuito) as cliente, pytest.raises(ErrorFuente) as info:
        cliente.buscar_por_radicado(RADICADO)
    assert info.value.codigo == 404
    assert "23 digitos" in str(info.value)
    assert ruta.call_count == 1
    assert reloj.esperas == []
    assert circuito.estado == Cortacircuito.CERRADO


@respx.mock
def test_429_respeta_retry_after(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=dict_busqueda([dict_proceso()])),
        ]
    )
    with hacer_cliente(reloj) as cliente:
        procesos = cliente.buscar_por_radicado(RADICADO)
    assert len(procesos) == 1
    assert ruta.call_count == 2
    assert reloj.esperas == [3.0]


@respx.mock
def test_503_persistente_agota_los_reintentos_con_retroceso_exponencial(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    with hacer_cliente(reloj) as cliente, pytest.raises(FuenteNoDisponible) as info:
        cliente.buscar_por_radicado(RADICADO)
    assert ruta.call_count == 3, "intentos_max=3"
    assert reloj.esperas == [1.0, 2.0], "base 1 s, factor 2, sin fluctuación (aleatorio=1.0)"
    assert info.value.codigo == 503


@respx.mock
def test_tiempo_de_espera_se_reintenta(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(
        side_effect=[httpx.ReadTimeout("lento"), httpx.Response(200, json=dict_busqueda([dict_proceso()]))]
    )
    with hacer_cliente(reloj) as cliente:
        assert len(cliente.buscar_por_radicado(RADICADO)) == 1
    assert ruta.call_count == 2
    assert reloj.esperas == [1.0]


@respx.mock
def test_error_de_conexion_persistente(reloj):
    respx.get(URL_BUSQUEDA).mock(side_effect=httpx.ConnectError("sin red"))
    with hacer_cliente(reloj) as cliente, pytest.raises(FuenteNoDisponible) as info:
        cliente.buscar_por_radicado(RADICADO)
    assert info.value.codigo is None
    assert "sin red" in str(info.value)


@respx.mock
def test_cortacircuito_se_abre_bloquea_y_se_recupera(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    circuito = Cortacircuito(umbral_fallos=2, segundos_abierto=60, reloj=reloj)
    reintentos = PoliticaReintentos(intentos_max=2, espera_base=1.0, aleatorio=lambda: 1.0)
    with hacer_cliente(reloj, cortacircuito=circuito, reintentos=reintentos) as cliente:
        with pytest.raises(FuenteNoDisponible):
            cliente.buscar_por_radicado(RADICADO)
        assert ruta.call_count == 2
        assert circuito.estado == Cortacircuito.ABIERTO

        with pytest.raises(CircuitoAbierto):
            cliente.buscar_por_radicado(RADICADO)
        assert ruta.call_count == 2, "con el circuito abierto no se toca la fuente"

        reloj.avanzar(60)
        ruta.mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
        assert len(cliente.buscar_por_radicado(RADICADO)) == 1, "la sonda del estado semiabierto pasa"
        assert circuito.estado == Cortacircuito.CERRADO


@respx.mock
def test_presupuesto_diario_agotado_no_consulta(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
    presupuesto = PresupuestoDiario(1, ContadorMemoria())
    with hacer_cliente(reloj, presupuesto=presupuesto) as cliente:
        cliente.buscar_por_radicado(RADICADO)
        with pytest.raises(PresupuestoAgotado):
            cliente.buscar_por_radicado(RADICADO)
    assert ruta.call_count == 1
    assert presupuesto.restante() == 0


@respx.mock
def test_respuesta_que_no_es_json(reloj):
    respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, text="<html>mantenimiento</html>"))
    with hacer_cliente(reloj) as cliente, pytest.raises(ErrorFuente):
        cliente.buscar_por_radicado(RADICADO)


@respx.mock
def test_obtener_actuaciones_y_detalle(reloj):
    actuaciones = respx.get(URL_ACTUACIONES).mock(
        return_value=httpx.Response(
            200,
            json=dict_pagina_actuaciones(
                [dict_actuacion(2, 2, anotacion="AUTO ADMITE DEMANDA"), dict_actuacion(1, 1, actuacion="Radicación")],
                total=2,
            ),
        )
    )
    detalle = respx.get(URL_DETALLE).mock(return_value=httpx.Response(200, json=dict_detalle()))
    with hacer_cliente(reloj) as cliente:
        pagina = cliente.obtener_actuaciones(ID_PROCESO, pagina=2)
        ficha = cliente.obtener_detalle(ID_PROCESO)
    assert actuaciones.calls.last.request.url.params["pagina"] == "2"
    assert [a.consecutivo for a in pagina.actuaciones] == [2, 1]
    assert all(a.id_proceso == ID_PROCESO for a in pagina.actuaciones)
    assert detalle.called and ficha.ponente == "PONENTE DE PRUEBA"


@respx.mock
def test_el_limitador_espacia_las_solicitudes(reloj):
    respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
    limitador = LimitadorTasa(12, rafaga=1, reloj=reloj, dormir=reloj.dormir)
    with hacer_cliente(reloj, limitador=limitador) as cliente:
        cliente.buscar_por_radicado(RADICADO)
        cliente.buscar_por_radicado(RADICADO)
        cliente.buscar_por_radicado(RADICADO)
    assert reloj.esperas == [pytest.approx(5.0), pytest.approx(5.0)]


def test_cliente_http_inyectado_recibe_cabeceras_y_no_se_cierra(reloj):
    externo = httpx.Client(base_url=URL_BASE_CPNU, headers={"User-Agent": "Externo/2.0"})
    cliente = hacer_cliente(reloj, cliente_http=externo)
    assert externo.headers["User-Agent"] == "Externo/2.0", "no se pisa un agente ya definido"
    assert externo.headers["Accept"] == "application/json"
    cliente.cerrar()
    assert not externo.is_closed
    externo.close()


# --- el cortacircuito nunca queda bloqueado -------------------------------------------------


def _abrir_circuito(reloj, cliente, ruta, circuito) -> None:
    with pytest.raises(FuenteNoDisponible):
        cliente.buscar_por_radicado(RADICADO)
    assert circuito.estado == Cortacircuito.ABIERTO
    reloj.avanzar(60)
    assert circuito.estado == Cortacircuito.SEMIABIERTO


@respx.mock
def test_sonda_que_recibe_un_404_cierra_el_circuito(reloj):
    """Antes, un 4xx en la sonda dejaba el circuito SEMIABIERTO con la sonda tomada para siempre."""
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
    reintentos = PoliticaReintentos(intentos_max=1)
    with hacer_cliente(reloj, cortacircuito=circuito, reintentos=reintentos) as cliente:
        _abrir_circuito(reloj, cliente, ruta, circuito)
        ruta.mock(return_value=httpx.Response(404, json={"Message": "no existe"}))
        with pytest.raises(ErrorFuente):
            cliente.buscar_por_radicado(RADICADO)
        assert circuito.estado == Cortacircuito.CERRADO, "la fuente respondió: el circuito se cierra"
        ruta.mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
        assert len(cliente.buscar_por_radicado(RADICADO)) == 1


@respx.mock
def test_403_cuenta_como_fallo_y_reabre_el_circuito(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
    with hacer_cliente(reloj, cortacircuito=circuito, reintentos=PoliticaReintentos(intentos_max=1)) as cliente:
        _abrir_circuito(reloj, cliente, ruta, circuito)
        ruta.mock(return_value=httpx.Response(403))
        with pytest.raises(ErrorFuente) as info:
            cliente.buscar_por_radicado(RADICADO)
        assert info.value.codigo == 403
        assert circuito.estado == Cortacircuito.ABIERTO, "un 403 puede ser un bloqueo: se vuelve a esperar"
        reloj.avanzar(60)
        ruta.mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
        assert len(cliente.buscar_por_radicado(RADICADO)) == 1
        assert circuito.estado == Cortacircuito.CERRADO


@respx.mock
def test_presupuesto_agotado_durante_la_sonda_la_libera(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
    presupuesto = PresupuestoDiario(1, ContadorMemoria())
    with hacer_cliente(
        reloj, cortacircuito=circuito, reintentos=PoliticaReintentos(intentos_max=1), presupuesto=presupuesto
    ) as cliente:
        _abrir_circuito(reloj, cliente, ruta, circuito)
        with pytest.raises(PresupuestoAgotado):
            cliente.buscar_por_radicado(RADICADO)
        assert ruta.call_count == 1
        assert circuito.permitir(), "la sonda quedó libre para cuando haya presupuesto"


@respx.mock
def test_mensaje_del_circuito_con_sonda_en_curso(reloj):
    respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(503))
    circuito = Cortacircuito(umbral_fallos=1, segundos_abierto=60, reloj=reloj)
    with hacer_cliente(reloj, cortacircuito=circuito, reintentos=PoliticaReintentos(intentos_max=1)) as cliente:
        _abrir_circuito(reloj, cliente, None, circuito)
        assert circuito.permitir()
        with pytest.raises(CircuitoAbierto, match="consulta de prueba en curso"):
            cliente.buscar_por_radicado(RADICADO)


@respx.mock
def test_agente_usuario_con_tildes_no_rompe_las_solicitudes(reloj):
    ruta = respx.get(URL_BUSQUEDA).mock(return_value=httpx.Response(200, json=dict_busqueda([dict_proceso()])))
    with ClienteCPNU(agente_usuario="Consultor/1.0 (vigilancia; contacto: oficina@ejemplo.com, Bogotá)", dormir=reloj.dormir) as cliente:
        assert len(cliente.buscar_por_radicado(RADICADO)) == 1
    assert ruta.calls[0].request.headers["User-Agent"] == "Consultor/1.0 (vigilancia; contacto: oficina@ejemplo.com, Bogota)"
