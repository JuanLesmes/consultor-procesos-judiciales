import io
import json
from datetime import date, datetime

import httpx
import pytest
import respx

from apoyo import RADICADO, hacer_actuacion, hacer_proceso
from consultor_procesos.adaptadores.notificacion.archivo import NotificadorArchivoJSONL
from consultor_procesos.adaptadores.notificacion.compuesto import NotificadorCompuesto
from consultor_procesos.adaptadores.notificacion.consola import NotificadorConsola
from consultor_procesos.adaptadores.notificacion.correo import ConfiguracionCorreo, NotificadorCorreo
from consultor_procesos.adaptadores.notificacion.formato import asunto_evento, formatear_evento
from consultor_procesos.adaptadores.notificacion.webhook import NotificadorWebhook
from consultor_procesos.dominio.modelos import EventoNovedades, Novedad

MOMENTO = datetime(2026, 9, 2, 10, 30)


def hacer_evento(con_auto: bool = True, linea_base: bool = False) -> EventoNovedades:
    novedades = [
        Novedad(RADICADO, hacer_actuacion(1, 1, "Recepción memorial", "SOLICITUD DE COPIAS", date(2026, 9, 1)), es_auto=False),
    ]
    if con_auto:
        novedades.append(
            Novedad(
                RADICADO,
                hacer_actuacion(2, 2, "Constancia secretarial", "AUTO FIJA FECHA AUDIENCIA", date(2026, 9, 2), con_documentos=True),
                es_auto=True,
                coincidencias=("AUTO",),
                despacho="JUZGADO DE PRUEBA",
            )
        )
    return EventoNovedades(RADICADO, "Demo", [hacer_proceso()], novedades, MOMENTO, es_linea_base=linea_base)


class TestFormato:
    def test_texto_marca_autos_y_documentos(self):
        texto = formatear_evento(hacer_evento())
        assert f"Radicado {RADICADO} (Demo)" in texto
        assert "[AUTO] 2026-09-02 #2    Constancia secretarial - AUTO FIJA FECHA AUDIENCIA (con documentos)" in texto
        assert "[    ] 2026-09-01 #1    Recepción memorial - SOLICITUD DE COPIAS" in texto
        assert "2 actuacion(es) nueva(s), 1 auto(s)" in texto
        assert "linea base" not in texto

    def test_linea_base_se_anuncia(self):
        assert "linea base" in formatear_evento(hacer_evento(linea_base=True))

    def test_solo_autos_filtra(self):
        texto = formatear_evento(hacer_evento(), solo_autos=True)
        assert "[AUTO]" in texto and "[    ]" not in texto
        assert formatear_evento(hacer_evento(con_auto=False), solo_autos=True) == ""

    def test_asunto(self):
        assert asunto_evento(hacer_evento()) == f"[Consultor de Procesos] 1 auto(s) nuevo(s) en {RADICADO} (Demo)"
        assert "1 actuacion(es) nueva(s)" in asunto_evento(hacer_evento(con_auto=False))


class TestConsola:
    def test_escribe_en_la_salida_indicada(self):
        salida = io.StringIO()
        NotificadorConsola(salida).notificar(hacer_evento())
        assert "[AUTO]" in salida.getvalue() and RADICADO in salida.getvalue()

    def test_solo_autos_omite_eventos_sin_autos(self):
        salida = io.StringIO()
        NotificadorConsola(salida, solo_autos=True).notificar(hacer_evento(con_auto=False))
        assert salida.getvalue() == ""


class TestArchivoJSONL:
    def test_una_linea_por_novedad(self, tmp_path):
        ruta = tmp_path / "salida" / "novedades.jsonl"
        notificador = NotificadorArchivoJSONL(ruta)
        notificador.notificar(hacer_evento())
        notificador.notificar(hacer_evento(con_auto=False))
        lineas = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines()]
        assert len(lineas) == 3
        auto = next(l for l in lineas if l["es_auto"])
        assert auto["radicado"] == RADICADO and auto["alias"] == "Demo"
        assert auto["despacho"] == "JUZGADO DE PRUEBA" and auto["coincidencias"] == ["AUTO"]
        assert auto["actuacion"]["anotacion"] == "AUTO FIJA FECHA AUDIENCIA"
        assert auto["actuacion"]["fecha_actuacion"] == "2026-09-02"
        assert auto["momento"] == MOMENTO.isoformat()

    def test_solo_autos(self, tmp_path):
        ruta = tmp_path / "n.jsonl"
        NotificadorArchivoJSONL(ruta, solo_autos=True).notificar(hacer_evento())
        assert len(ruta.read_text(encoding="utf-8").splitlines()) == 1


class TestCompuesto:
    def test_aisla_los_fallos(self):
        class Roto:
            def notificar(self, evento):
                raise RuntimeError("smtp caído")

        recibidos = []

        class Bueno:
            def notificar(self, evento):
                recibidos.append(evento)

        compuesto = NotificadorCompuesto([Roto(), Bueno()])
        compuesto.notificar(hacer_evento())
        assert len(recibidos) == 1
        assert [nombre for nombre, _ in compuesto.errores] == ["Roto"]


class TestCorreo:
    def test_construye_el_mensaje_y_usa_el_transporte(self):
        enviados = []
        config = ConfiguracionCorreo(
            servidor="smtp.ejemplo.com", remitente="bot@ejemplo.com", destinatarios=("a@ejemplo.com", "b@ejemplo.com")
        )
        NotificadorCorreo(config, transporte=enviados.append).notificar(hacer_evento())
        [mensaje] = enviados
        assert mensaje["Subject"].startswith("[Consultor de Procesos] 1 auto(s)")
        assert mensaje["From"] == "bot@ejemplo.com" and mensaje["To"] == "a@ejemplo.com, b@ejemplo.com"
        cuerpo = mensaje.get_content()
        assert "[AUTO]" in cuerpo and "[    ]" not in cuerpo, "por defecto solo se envían autos"
        assert "consultaprocesos.ramajudicial.gov.co" in cuerpo

    def test_sin_autos_no_envia_cuando_solo_autos(self):
        enviados = []
        config = ConfiguracionCorreo(servidor="smtp.ejemplo.com", destinatarios=("a@ejemplo.com",), solo_autos=True)
        NotificadorCorreo(config, transporte=enviados.append).notificar(hacer_evento(con_auto=False))
        assert enviados == []

    def test_requiere_destinatarios(self):
        with pytest.raises(ValueError):
            NotificadorCorreo(ConfiguracionCorreo(servidor="smtp.ejemplo.com"))


class TestWebhook:
    @respx.mock
    def test_envia_post_json(self):
        ruta = respx.post("https://hooks.ejemplo.com/abc").mock(return_value=httpx.Response(200))
        NotificadorWebhook("https://hooks.ejemplo.com/abc").notificar(hacer_evento())
        assert ruta.called
        carga = json.loads(ruta.calls.last.request.content)
        assert carga["radicado"] == RADICADO and carga["total_autos"] == 1 and len(carga["novedades"]) == 2
        assert "[AUTO]" in carga["text"]

    @respx.mock
    def test_error_http_se_propaga(self):
        respx.post("https://hooks.ejemplo.com/abc").mock(return_value=httpx.Response(500))
        with pytest.raises(httpx.HTTPStatusError):
            NotificadorWebhook("https://hooks.ejemplo.com/abc").notificar(hacer_evento())

    def test_requiere_url(self):
        with pytest.raises(ValueError):
            NotificadorWebhook("")
