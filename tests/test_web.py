"""Pruebas de la interfaz web: la aplicación se ejercita sin puertos y el transporte HTTP con un servidor real."""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from apoyo import ID_PROCESO, PDF_MINIMO, RADICADO, FuenteFalsa, NotificadorRegistro, hacer_actuacion, hacer_documento, hacer_proceso
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.adaptadores.web.servidor import AplicacionWeb, ServidorWeb
from consultor_procesos.aplicacion.planificador import Planificador
from consultor_procesos.aplicacion.servicio_vigilancia import OpcionesVerificacion, ServicioVigilancia
from consultor_procesos.infraestructura.cortesia import ContadorMemoria, PresupuestoDiario

AHORA = datetime(2026, 9, 2, 10, 0)


class Cliente:
    """Envoltura cómoda sobre `AplicacionWeb.manejar`."""

    def __init__(self, aplicacion: AplicacionWeb) -> None:
        self.aplicacion = aplicacion

    def __call__(self, metodo: str, ruta: str, cuerpo: dict | None = None):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(ruta)
        estado, contenido, tipo, cabeceras = self.aplicacion.manejar(metodo, url.path, parse_qs(url.query), cuerpo)
        return estado, contenido, tipo, cabeceras

    def json(self, metodo: str, ruta: str, cuerpo: dict | None = None, esperado: int = 200):
        estado, contenido, tipo, _ = self(metodo, ruta, cuerpo)
        assert estado == esperado, (estado, contenido)
        assert tipo.startswith("application/json")
        return contenido


def esperar_verificacion(aplicacion: AplicacionWeb, segundos: float = 5.0) -> None:
    limite = time.monotonic() + segundos
    while aplicacion.trabajo.en_curso or aplicacion._candado_fuente.locked():
        assert time.monotonic() < limite, "la verificación no terminó a tiempo"
        time.sleep(0.01)


@pytest.fixture
def aplicacion(fuente: FuenteFalsa, notificador: NotificadorRegistro, tmp_path: Path) -> AplicacionWeb:
    repo = RepositorioMemoria()
    servicio = ServicioVigilancia(
        fuente,
        repo,
        notificador,
        opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0),
        ahora=lambda: AHORA,
        dormir=lambda s: None,
        directorio_documentos=tmp_path / "docs",
    )
    presupuesto = PresupuestoDiario(100, ContadorMemoria(), hoy=lambda: AHORA.date())

    def fabrica(ciclo):
        return Planificador(ciclo, intervalo_segundos=3600, jitter_fraccion=0.0)

    return AplicacionWeb(servicio, repo, fabrica_planificador=fabrica, presupuesto=presupuesto, segundos_actualizacion=7, ahora=lambda: AHORA)


@pytest.fixture
def cliente(aplicacion: AplicacionWeb) -> Cliente:
    return Cliente(aplicacion)


def registrar_proceso(fuente: FuenteFalsa) -> None:
    fuente.registrar(
        hacer_proceso(fecha_ultima=date(2024, 5, 10)),
        [
            hacer_actuacion(1, 1, "Radicación de proceso", "", date(2024, 4, 1)),
            hacer_actuacion(2, 2, "Constancia secretarial", "AUTO ADMITE DEMANDA", date(2024, 5, 10), con_documentos=True),
        ],
    )
    fuente.registrar_documento(hacer_documento(501, 2, "Auto admite demanda.pdf"))


def test_indice_y_rutas_desconocidas(cliente: Cliente):
    estado, contenido, tipo, _ = cliente("GET", "/")
    assert estado == 200 and tipo.startswith("text/html") and b"<title>Consultor de Procesos</title>" in contenido
    assert cliente("GET", "/no-existe")[0] == 404
    assert cliente("DELETE", "/api/estado")[0] == 405


def test_estado_inicial(cliente: Cliente):
    estado = cliente.json("GET", "/api/estado")
    assert estado["vigilados"] == [] and estado["pendientes"] == {"total": 0, "autos": 0, "publicaciones": 0}
    assert estado["presupuesto"] == {"usado": 0, "maximo": 100}
    assert estado["vigilante"] == {"disponible": True, "activo": False, "ciclos": 0, "proxima_ejecucion": None, "horario": None}
    assert estado["verificacion"]["en_curso"] is False
    assert estado["segundos_actualizacion"] == 7 and estado["portal"].startswith("https://")


def test_vigilados_alta_baja_y_validacion(cliente: Cliente):
    creado = cliente.json("POST", "/api/vigilados", {"radicado": "11001 4003 001 2024 00123 45", "alias": "Demo"}, esperado=201)
    assert creado["radicado"] == RADICADO and creado["alias"] == "Demo" and creado["inicializado"] is False
    assert [v["radicado"] for v in cliente.json("GET", "/api/vigilados")] == [RADICADO]
    estado, contenido, *_ = cliente("POST", "/api/vigilados", {"radicado": "123"})
    assert estado == 400 and "23 dígitos" in contenido["error"]
    assert cliente("POST", "/api/vigilados", {})[0] == 400
    assert cliente.json("DELETE", f"/api/vigilados/{RADICADO}")["eliminado"] is True
    assert cliente.json("DELETE", f"/api/vigilados/{RADICADO}")["eliminado"] is False


def test_verificacion_en_segundo_plano_y_novedades(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO, "alias": "Demo"}, esperado=201)
    respuesta = cliente.json("POST", "/api/verificar", {}, esperado=202)
    assert respuesta["iniciado"] is True
    esperar_verificacion(aplicacion)

    estado_verificacion = cliente.json("GET", "/api/verificar/estado")
    assert estado_verificacion["en_curso"] is False and estado_verificacion["error"] is None
    [resumen] = estado_verificacion["resumen"]
    assert resumen["estado"] == "OK" and resumen["linea_base"] is True and resumen["autos"] == 1

    novedades = cliente.json("GET", "/api/novedades")
    assert len(novedades) == 2
    auto = next(n for n in novedades if n["es_auto"])
    assert auto["alias"] == "Demo" and auto["revisada"] is False and auto["con_documentos"] is True
    assert auto["enlaces"]["detalle"] == f"/api/novedades/{auto['id_registro']}"
    assert auto["enlaces"]["portal"].startswith("https://consultaprocesos.ramajudicial.gov.co")
    assert [n["id_registro"] for n in cliente.json("GET", "/api/novedades?solo_autos=1")] == [auto["id_registro"]]
    assert cliente.json("GET", f"/api/novedades?radicado={RADICADO}&limite=1")[0]["id_registro"] in (1, 2)

    estado = cliente.json("GET", "/api/estado")
    assert estado["pendientes"] == {"total": 2, "autos": 1, "publicaciones": 0}
    assert estado["vigilados"][0]["autos_pendientes"] == 1 and estado["vigilados"][0]["inicializado"] is True
    assert estado["presupuesto"]["usado"] == 0, "la fuente falsa no consume presupuesto; el contador es de la CPNU real"

    cliente.json("POST", f"/api/novedades/{auto['id_registro']}/revisada", {"revisada": True})
    assert cliente.json("GET", "/api/estado")["pendientes"] == {"total": 1, "autos": 0, "publicaciones": 0}
    assert cliente.json("GET", f"/api/novedades/{auto['id_registro']}")["revisada"] is True
    assert cliente("POST", "/api/novedades/999/revisada", {})[0] == 404
    assert cliente("GET", "/api/novedades/999")[0] == 404
    assert cliente("GET", "/api/novedades?limite=abc")[0] == 400


def test_documentos_y_descarga(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa, tmp_path: Path):
    registrar_proceso(fuente)
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO}, esperado=201)
    cliente.json("POST", "/api/verificar", {}, esperado=202)
    esperar_verificacion(aplicacion)

    documentos = cliente.json("GET", "/api/novedades/2/documentos")
    assert [d["id_documento"] for d in documentos] == [501]
    assert documentos[0]["url_vista"] == "/api/documentos/501"
    assert documentos[0]["url_descarga"] == "/api/documentos/501?descargar=1"
    assert documentos[0]["url_fuente"].endswith("/Descarga/DocumentoActuacion/501")
    assert cliente.json("GET", "/api/novedades/1/documentos") == [], "sin documentos no se consulta la fuente"
    assert ("documentos", 1) not in fuente.llamadas

    estado, contenido, tipo, cabeceras = cliente("GET", "/api/documentos/501")
    assert estado == 200 and contenido == PDF_MINIMO and tipo == "application/pdf"
    assert cabeceras["Content-Disposition"].startswith("inline;")
    assert (tmp_path / "docs" / "501.pdf").is_file()
    estado, _, _, cabeceras = cliente("GET", "/api/documentos/501?descargar=1")
    assert cabeceras["Content-Disposition"].startswith("attachment;") and "Auto" in cabeceras["Content-Disposition"]
    assert cliente("GET", "/api/documentos/999")[0] == 404


def test_consulta_puntual(cliente: Cliente, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    datos = cliente.json("GET", f"/api/consultar/{RADICADO}?max_paginas=2&sin_detalle=1")
    assert datos["resumen"] == {"total_actuaciones": 2, "total_autos": 1} and datos["detalles"] == []
    assert datos["alias"] is None and datos["enlace_portal"].startswith("https://")
    assert cliente("GET", "/api/consultar/05001310300320230067890")[0] == 404
    assert cliente("GET", "/api/consultar/123")[0] == 400


def test_fuente_ocupada_devuelve_409(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    assert aplicacion._candado_fuente.acquire(blocking=False)
    try:
        estado, contenido, *_ = cliente("GET", f"/api/consultar/{RADICADO}")
        assert estado == 409 and "en curso" in contenido["error"]
        assert cliente("POST", "/api/verificar", {})[0] == 409
    finally:
        aplicacion._candado_fuente.release()


def test_vigilante_iniciar_y_detener(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO}, esperado=201)
    vigilante = cliente.json("POST", "/api/vigilante/iniciar", {})
    assert vigilante["activo"] is True
    limite = time.monotonic() + 5
    while cliente.json("GET", "/api/verificar/estado")["terminado_en"] is None:
        assert time.monotonic() < limite
        time.sleep(0.01)
    assert cliente.json("GET", "/api/verificar/estado")["origen"] == "vigilancia"
    estado = cliente.json("GET", "/api/estado")["vigilante"]
    assert estado["ciclos"] >= 1 and estado["proxima_ejecucion"] is not None
    assert cliente.json("POST", "/api/vigilante/detener", {})["activo"] is False


def test_historial(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO}, esperado=201)
    cliente.json("POST", "/api/verificar", {"radicados": [RADICADO]}, esperado=202)
    esperar_verificacion(aplicacion)
    filas = cliente.json("GET", f"/api/historial?radicado={RADICADO}&limite=5")
    assert len(filas) == 1 and filas[0]["estado"] == "OK" and filas[0]["momento"].startswith("2026-09-02")
    assert cliente("POST", "/api/verificar", {"radicados": "no-lista"})[0] == 400


def test_segundo_nivel_ficha_e_historial_del_proceso(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    assert cliente("GET", f"/api/procesos/{RADICADO}")[0] == 404
    assert cliente("GET", "/api/procesos/123")[0] == 400
    creado = cliente.json("POST", "/api/vigilados", {"radicado": RADICADO}, esperado=201)
    assert creado["titulo"] == f"Proceso {RADICADO}" and creado["despacho"] == "" and creado["despachos"] == [RADICADO[:12]]
    assert creado["enlaces"]["origen_ficha"] is None, "sin id de proceso todavía"

    cliente.json("POST", "/api/verificar", {"radicados": [RADICADO]}, esperado=202)
    esperar_verificacion(aplicacion)
    datos = cliente.json("GET", f"/api/procesos/{RADICADO}")
    proceso = datos["proceso"]
    assert proceso["titulo"] == "Demandante: PERSONA DE PRUEBA | Demandado: ENTIDAD DE PRUEBA", "sin alias, el título son los sujetos"
    assert proceso["despacho"] == "JUZGADO 001 CIVIL MUNICIPAL DE PRUEBA" and proceso["ponente"] == "PONENTE DE PRUEBA"
    assert proceso["fecha_proceso"] == "2024-01-15" and proceso["autos_pendientes"] == 1
    assert proceso["enlaces"]["origen_actuaciones"] == f"https://consultaprocesos.ramajudicial.gov.co:448/api/v2/Proceso/Actuaciones/{ID_PROCESO}?pagina=1"
    assert proceso["enlaces"]["origen_ficha"].endswith(f"/Proceso/Detalle/{ID_PROCESO}")
    assert [a["consecutivo"] for a in datos["actuaciones"]] == [2, 1], "historial completo, lo más reciente primero"
    assert datos["actuaciones"][0]["enlaces"]["documento"] == "/documento/2"
    assert datos["publicaciones"] == [] and datos["verificaciones"][0]["estado"] == "OK"
    assert datos["enlaces"]["portal"].startswith("https://")

    renombrado = cliente.json("POST", f"/api/procesos/{RADICADO}/alias", {"alias": "Caso demo"})
    assert renombrado["alias"] == "Caso demo" and renombrado["titulo"] == "Caso demo"
    assert cliente.json("GET", "/api/estado")["vigilados"][0]["titulo"] == "Caso demo"
    sin_alias = cliente.json("POST", f"/api/procesos/{RADICADO}/alias", {"alias": "  "})
    assert sin_alias["alias"] is None and sin_alias["titulo"].startswith("Demandante")
    assert cliente("POST", "/api/procesos/05001310300320230067890/alias", {"alias": "x"})[0] == 404


def test_pagina_visor_abre_el_documento_del_auto(cliente: Cliente, aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO, "alias": "Demo"}, esperado=201)
    cliente.json("POST", "/api/verificar", {}, esperado=202)
    esperar_verificacion(aplicacion)

    estado, contenido, tipo, _ = cliente("GET", "/documento/2")
    pagina = contenido.decode("utf-8")
    assert estado == 200 and tipo.startswith("text/html")
    assert '<iframe src="/api/documentos/501"' in pagina, "abre el PDF del auto directamente"
    assert "/Descarga/DocumentoActuacion/501" in pagina, "enlace a la descarga oficial en la Rama Judicial"
    assert "AUTO ADMITE DEMANDA" in pagina and "Demo" in pagina and 'href="/#/proceso/' + RADICADO in pagina
    assert ("documentos", 2) in fuente.llamadas, "la lista se pidió a la fuente una sola vez"

    estado, contenido, *_ = cliente("GET", "/documento/2/501")
    assert estado == 200 and b'<iframe src="/api/documentos/501"' in contenido
    assert fuente.llamadas.count(("documentos", 2)) == 1, "la segunda vez sale de la base local"

    estado, contenido, *_ = cliente("GET", "/documento/1")
    pagina = contenido.decode("utf-8")
    assert estado == 200 and "<iframe" not in pagina and "no tiene documentos publicados" in pagina
    assert ("documentos", 1) not in fuente.llamadas

    estado, contenido, tipo, _ = cliente("GET", "/documento/999")
    assert estado == 404 and tipo.startswith("text/html") and b"No hay ninguna actuaci" in contenido


def test_estado_incluye_el_horario_de_vigilancia(fuente: FuenteFalsa, notificador: NotificadorRegistro):
    from consultor_procesos.aplicacion.horario import Horario

    repo = RepositorioMemoria()
    servicio = ServicioVigilancia(fuente, repo, notificador, ahora=lambda: AHORA, dormir=lambda s: None)
    horario = Horario.desde_texto(["07:00", "17:00"], hasta="18:00")
    aplicacion = AplicacionWeb(servicio, repo, ahora=lambda: AHORA, horario=horario)
    vigilante = Cliente(aplicacion).json("GET", "/api/estado")["vigilante"]
    assert vigilante["disponible"] is False
    assert vigilante["horario"]["horas"] == ["07:00", "17:00"] and vigilante["horario"]["hasta"] == "18:00"
    assert vigilante["horario"]["descripcion"] == "de lunes a viernes a las 07:00 y 17:00"


def test_servidor_http_real(aplicacion: AplicacionWeb, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    servidor = ServidorWeb(aplicacion, host="127.0.0.1", puerto=0)
    host, puerto = servidor.iniciar()
    try:
        cabeceras = {"X-Requested-With": "XMLHttpRequest"}
        with httpx.Client(base_url=f"http://{host}:{puerto}", timeout=5, headers=cabeceras) as http:
            pagina = http.get("/")
            assert pagina.status_code == 200 and "Consultor de Procesos" in pagina.text
            assert http.get("/api/estado").json()["vigilados"] == []
            creado = http.post("/api/vigilados", json={"radicado": RADICADO, "alias": "HTTP"})
            assert creado.status_code == 201 and creado.json()["alias"] == "HTTP"
            assert http.post("/api/vigilados", content=b"no es json", headers={"Content-Type": "application/json"}).status_code == 400
            assert http.post("/api/verificar", json={}).status_code == 202
            esperar_verificacion(aplicacion)
            novedades = http.get("/api/novedades", params={"solo_autos": "1"}).json()
            assert len(novedades) == 1 and "AUTO ADMITE" in novedades[0]["anotacion"]
            documentos = http.get(f"/api/novedades/{novedades[0]['id_registro']}/documentos").json()
            assert [d["id_documento"] for d in documentos] == [501]
            descarga = http.get("/api/documentos/501")
            assert descarga.status_code == 200 and descarga.headers["content-type"] == "application/pdf"
            assert descarga.content == PDF_MINIMO
            visor = http.get(f"/documento/{novedades[0]['id_registro']}")
            assert visor.status_code == 200 and "text/html" in visor.headers["content-type"] and "/api/documentos/501" in visor.text
            assert http.get(f"/api/procesos/{RADICADO}").json()["proceso"]["alias"] == "HTTP"
            assert http.delete(f"/api/vigilados/{RADICADO}").json()["eliminado"] is True
    finally:
        servidor.detener()


# --- publicaciones procesales ------------------------------------------------------------------


@pytest.fixture
def aplicacion_pub(fuente: FuenteFalsa, notificador: NotificadorRegistro, tmp_path: Path):
    from apoyo import FuentePublicacionesFalsa
    from consultor_procesos.aplicacion.servicio_publicaciones import OpcionesPublicaciones, ServicioPublicaciones

    repo = RepositorioMemoria()
    fuente_pub = FuentePublicacionesFalsa()
    servicio_pub = ServicioPublicaciones(
        fuente_pub, repo, notificador, opciones=OpcionesPublicaciones(tipos=(6098957,), pausa_entre_despachos_segundos=0), ahora=lambda: AHORA
    )
    servicio = ServicioVigilancia(
        fuente,
        repo,
        notificador,
        opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0, descubrir_despachos=False),
        ahora=lambda: AHORA,
        dormir=lambda s: None,
        publicaciones=servicio_pub,
    )
    return AplicacionWeb(servicio, repo, ahora=lambda: AHORA), fuente_pub


def test_publicaciones_desactivadas_devuelven_400(cliente: Cliente):
    assert cliente("GET", "/api/publicaciones")[0] == 400
    assert cliente.json("GET", "/api/estado")["publicaciones"] == {"habilitado": False}


def test_publicaciones_revision_y_coincidencias(aplicacion_pub, fuente: FuenteFalsa):
    from apoyo import DESPACHO_CODIGO, hacer_publicacion

    aplicacion, fuente_pub = aplicacion_pub
    cliente = Cliente(aplicacion)
    registrar_proceso(fuente)
    fuente_pub.registrar(hacer_publicacion("1", titulo=f"Estado con {RADICADO}", documentos=()))
    cliente.json("POST", "/api/vigilados", {"radicado": RADICADO, "alias": "Demo"}, esperado=201)
    assert cliente.json("GET", "/api/publicaciones") == []
    assert cliente.json("GET", "/api/estado")["publicaciones"] == {"habilitado": True}

    assert cliente.json("POST", "/api/publicaciones/revisar", {}, esperado=202)["iniciado"] is True
    esperar_verificacion(aplicacion)
    estado_trabajo = cliente.json("GET", "/api/verificar/estado")
    assert estado_trabajo["origen"] == "publicaciones" and estado_trabajo["error"] is None
    [resumen] = estado_trabajo["publicaciones"]
    assert resumen["despacho_codigo"] == DESPACHO_CODIGO and resumen["estado"] == "OK" and resumen["coincidencias"] == 1

    [coincidencia] = cliente.json("GET", "/api/publicaciones")
    assert coincidencia["radicado"] == RADICADO and coincidencia["alias"] == "Demo" and coincidencia["revisada"] is False
    assert coincidencia["publicacion"]["titulo"].startswith("Estado con") and coincidencia["publicacion"]["url_detalle"].startswith("https://")
    assert cliente.json("GET", "/api/estado")["pendientes"]["publicaciones"] == 1
    assert cliente.json("GET", f"/api/publicaciones?radicado={RADICADO}&pendientes=1") == [coincidencia]

    cliente.json("POST", coincidencia["enlaces"]["revisada"], {"revisada": True})
    assert cliente.json("GET", "/api/estado")["pendientes"]["publicaciones"] == 0
    assert cliente("POST", "/api/publicaciones/999/revisada", {})[0] == 404
    [publicacion] = cliente.json("GET", f"/api/publicaciones/despacho/{DESPACHO_CODIGO}")
    assert publicacion["id_publicacion"] == "1"

    # La verificación normal también revisa publicaciones y lo refleja en el resumen.
    cliente.json("POST", "/api/verificar", {}, esperado=202)
    esperar_verificacion(aplicacion)
    trabajo = cliente.json("GET", "/api/verificar/estado")
    assert trabajo["resumen"][0]["publicaciones"] == 0, "la coincidencia ya se había registrado"
    assert trabajo["publicaciones"][0]["estado"] == "SIN_CAMBIOS"


def test_dos_servidores_no_comparten_puerto(aplicacion: AplicacionWeb):
    import sys

    primero = ServidorWeb(aplicacion, host="127.0.0.1", puerto=0)
    host, puerto = primero.iniciar()
    segundo = ServidorWeb(aplicacion, host=host, puerto=puerto)
    try:
        if sys.platform == "win32":
            with pytest.raises(OSError):
                segundo.iniciar()
        else:
            pytest.skip("en otros sistemas el puerto ocupado ya falla por sí solo")
    finally:
        primero.detener()
        try:
            segundo.detener()
        except Exception:  # noqa: BLE001 - nunca llegó a arrancar
            pass
