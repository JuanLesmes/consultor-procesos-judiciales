"""Novedades pendientes, documentos de actuaciones y su caché: analizador, cliente, repositorios y servicio."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from apoyo import (
    DESPACHO,
    ID_PROCESO,
    PDF_MINIMO,
    RADICADO,
    RADICADO_2,
    FuenteFalsa,
    NotificadorRegistro,
    RelojCalendario,
    RelojFalso,
    dict_documento,
    hacer_actuacion,
    hacer_documento,
    hacer_proceso,
)
from consultor_procesos.adaptadores.cpnu.analizador import analizar_documentos
from consultor_procesos.adaptadores.cpnu.cliente import ClienteCPNU, nombre_desde_disposicion
from consultor_procesos.adaptadores.notificacion.formato import formatear_evento, linea_novedad, serializar_novedad
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.adaptadores.persistencia.sqlite import RepositorioSQLite
from consultor_procesos.aplicacion.servicio_vigilancia import OpcionesVerificacion, ServicioVigilancia
from consultor_procesos.dominio.errores import DocumentoNoEncontrado, ErrorFuente, RespuestaInesperada
from consultor_procesos.dominio.modelos import EventoNovedades, Novedad, ProcesoVigilado
from consultor_procesos.enlaces import URL_BASE_CPNU, url_descarga_documento
from consultor_procesos.infraestructura.cortesia import LimitadorTasa, PoliticaReintentos

AHORA = datetime(2026, 9, 2, 10, 0)


# --- analizador -------------------------------------------------------------------------------


class TestAnalizadorDocumentos:
    def test_lista_con_la_forma_del_portal(self):
        documentos = analizar_documentos([dict_documento(501, "Auto admite.pdf"), dict_documento(502, "Anexo.pdf", None)], 77)
        assert [d.id_documento for d in documentos] == [501, 502]
        assert documentos[0].nombre == "Auto admite.pdf" and documentos[0].id_registro == 77
        assert documentos[0].fecha == date(2026, 9, 1) and documentos[1].fecha is None

    def test_objeto_envolvente_y_nombres_alternativos(self):
        datos = {"documentos": [{"idDocumento": 9, "nombreDocumento": "x.pdf", "tamanio": 1234, "tipo": "pdf"}]}
        [documento] = analizar_documentos(datos, 1)
        assert documento.id_documento == 9 and documento.nombre == "x.pdf" and documento.tamano == 1234 and documento.tipo == "pdf"

    def test_vacios_se_toleran_y_elementos_con_id_nulo_se_omiten(self):
        assert analizar_documentos(None, 1) == []
        assert analizar_documentos({}, 1) == []
        assert analizar_documentos([], 1) == []
        assert analizar_documentos([{"idRegDocumento": None, "nombre": "sin id"}], 1) == []

    def test_documento_sin_clave_de_identificador_falla_en_voz_alta(self):
        with pytest.raises(RespuestaInesperada, match="idRegDocumento"):
            analizar_documentos([{"nombre": "sin id"}], 1)
        with pytest.raises(RespuestaInesperada):
            analizar_documentos({"archivos": [dict_documento(1)]}, 1)

    def test_forma_invalida(self):
        with pytest.raises(ErrorFuente):
            analizar_documentos({"documentos": "no"}, 1)


# --- cliente ------------------------------------------------------------------------------------


def hacer_cliente(reloj: RelojFalso) -> ClienteCPNU:
    return ClienteCPNU(
        agente_usuario="PruebaUA/1.0",
        limitador=LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir),
        reintentos=PoliticaReintentos(intentos_max=2, espera_base=1.0, aleatorio=lambda: 1.0),
        dormir=reloj.dormir,
    )


@respx.mock
def test_cliente_lista_documentos(reloj):
    ruta = respx.get(f"{URL_BASE_CPNU}/Proceso/DocumentosActuacion/77").mock(
        return_value=httpx.Response(200, json=[dict_documento(501)])
    )
    with hacer_cliente(reloj) as cliente:
        documentos = cliente.listar_documentos(77)
    assert ruta.called and [d.id_documento for d in documentos] == [501]


@respx.mock
def test_cliente_descarga_documento_con_nombre(reloj):
    respx.get(f"{URL_BASE_CPNU}/Descarga/DocumentoActuacion/501").mock(
        return_value=httpx.Response(
            200,
            content=PDF_MINIMO,
            headers={"Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="Auto admite.pdf"'},
        )
    )
    with hacer_cliente(reloj) as cliente:
        descarga = cliente.descargar_documento(501)
    assert descarga.contenido == PDF_MINIMO
    assert descarga.nombre == "Auto admite.pdf" and descarga.tipo_contenido == "application/pdf"
    assert url_descarga_documento(501) == f"{URL_BASE_CPNU}/Descarga/DocumentoActuacion/501"


@respx.mock
def test_cliente_rechaza_descarga_en_json(reloj):
    respx.get(f"{URL_BASE_CPNU}/Descarga/DocumentoActuacion/501").mock(return_value=httpx.Response(200, json={"x": 1}))
    with hacer_cliente(reloj) as cliente, pytest.raises(ErrorFuente):
        cliente.descargar_documento(501)


def test_nombre_desde_disposicion():
    assert nombre_desde_disposicion('attachment; filename="a b.pdf"') == "a b.pdf"
    assert nombre_desde_disposicion("inline; filename=simple.pdf") == "simple.pdf"
    assert nombre_desde_disposicion("attachment; filename*=UTF-8''Auto%20n%C2%B01.pdf") == "Auto n°1.pdf"
    assert nombre_desde_disposicion(None) == "" and nombre_desde_disposicion("inline") == ""


# --- repositorios ---------------------------------------------------------------------------------


@pytest.fixture(params=["memoria", "sqlite"])
def repositorio(request, tmp_path):
    repo = RepositorioMemoria() if request.param == "memoria" else RepositorioSQLite(tmp_path / "p.sqlite")
    yield repo
    repo.cerrar()


def novedad(id_registro: int, consecutivo: int, es_auto: bool, radicado: str = RADICADO, con_documentos: bool = False, documentos=()) -> Novedad:
    actuacion = hacer_actuacion(id_registro, consecutivo, anotacion="AUTO" if es_auto else "OTRA", radicado=radicado, con_documentos=con_documentos)
    return Novedad(radicado, actuacion, es_auto, ("AUTO",) if es_auto else (), DESPACHO, documentos=tuple(documentos))


class TestNovedadesRecientes:
    def test_orden_por_deteccion_y_filtros(self, repositorio):
        t1, t2 = datetime(2026, 9, 1, 8), datetime(2026, 9, 2, 8)
        repositorio.guardar_novedades([novedad(1, 1, False), novedad(2, 2, True)], t1)
        repositorio.guardar_novedades([novedad(3, 1, True, radicado=RADICADO_2)], t2)
        recientes = repositorio.listar_novedades_recientes()
        assert [n.actuacion.id_registro for n in recientes] == [3, 2, 1]
        assert recientes[0].visto_en == t2 and recientes[0].revisada is False
        assert [n.actuacion.id_registro for n in repositorio.listar_novedades_recientes(solo_autos=True)] == [3, 2]
        assert [n.actuacion.id_registro for n in repositorio.listar_novedades_recientes(radicado=RADICADO)] == [2, 1]
        assert len(repositorio.listar_novedades_recientes(limite=1)) == 1

    def test_pendientes_y_revisadas(self, repositorio):
        repositorio.guardar_novedades([novedad(1, 1, False), novedad(2, 2, True)], AHORA)
        assert repositorio.contar_pendientes() == 2 and repositorio.contar_pendientes(solo_autos=True) == 1
        assert repositorio.marcar_revisada(2)
        assert repositorio.contar_pendientes(solo_autos=True) == 0
        assert [n.actuacion.id_registro for n in repositorio.listar_novedades_recientes(solo_pendientes=True)] == [1]
        assert repositorio.obtener_novedad(2).revisada is True
        assert repositorio.marcar_revisada(2, revisada=False) and repositorio.contar_pendientes() == 2
        assert not repositorio.marcar_revisada(999)
        assert repositorio.obtener_novedad(999) is None


class TestDocumentosRepositorio:
    def test_ciclo_completo(self, repositorio):
        repositorio.guardar_novedades([novedad(10, 1, True, con_documentos=True)], AHORA)
        assert repositorio.listar_documentos(10) is None, "aún no se ha consultado la fuente"
        repositorio.guardar_documentos(10, [hacer_documento(501, 10), hacer_documento(502, 10, "Anexo.pdf")], AHORA)
        documentos = repositorio.listar_documentos(10)
        assert [d.id_documento for d in documentos] == [501, 502]
        assert repositorio.obtener_documento(502).nombre == "Anexo.pdf"
        assert repositorio.obtener_novedad(10).documentos[0].id_documento == 501
        assert repositorio.listar_novedades_recientes()[0].documentos[1].nombre == "Anexo.pdf"
        assert repositorio.ruta_local_documento(501) is None
        repositorio.registrar_descarga(501, "documentos/501.pdf", AHORA)
        assert repositorio.ruta_local_documento(501) == "documentos/501.pdf"

    def test_lista_vacia_consultada_se_recuerda(self, repositorio):
        repositorio.guardar_novedades([novedad(10, 1, True, con_documentos=True)], AHORA)
        repositorio.guardar_documentos(10, [], AHORA)
        assert repositorio.listar_documentos(10) == []

    def test_guardar_novedad_con_documentos_los_persiste(self, repositorio):
        repositorio.guardar_novedades([novedad(10, 1, True, con_documentos=True, documentos=[hacer_documento(501, 10)])], AHORA)
        assert [d.id_documento for d in repositorio.listar_documentos(10)] == [501]

    def test_eliminar_vigilado_borra_documentos(self, repositorio):
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO))
        repositorio.guardar_novedades([novedad(10, 1, True, documentos=[hacer_documento(501, 10)])], AHORA)
        repositorio.eliminar_vigilado(RADICADO)
        assert repositorio.obtener_documento(501) is None


def test_sqlite_migra_bases_antiguas(tmp_path: Path):
    ruta = tmp_path / "antigua.sqlite"
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE actuaciones_vistas (
            id_registro INTEGER PRIMARY KEY, radicado TEXT NOT NULL, id_proceso INTEGER, consecutivo INTEGER NOT NULL DEFAULT 0,
            actuacion TEXT, anotacion TEXT, fecha_actuacion TEXT, fecha_registro TEXT, fecha_inicial TEXT, fecha_final TEXT,
            con_documentos INTEGER NOT NULL DEFAULT 0, es_auto INTEGER NOT NULL DEFAULT 0, coincidencias TEXT, despacho TEXT,
            visto_en TEXT NOT NULL
        );
        INSERT INTO actuaciones_vistas (id_registro, radicado, consecutivo, actuacion, es_auto, visto_en)
        VALUES (1, '11001400300120240012345', 1, 'Auto antiguo', 1, '2026-09-01T08:00:00');
        """
    )
    conexion.commit()
    conexion.close()
    with RepositorioSQLite(ruta) as repo:
        [novedad_antigua] = repo.listar_novedades_recientes()
        assert novedad_antigua.revisada is False and novedad_antigua.actuacion.actuacion == "Auto antiguo"
        assert repo.marcar_revisada(1) and repo.contar_pendientes() == 0
        assert repo.listar_documentos(1) is None


# --- servicio ---------------------------------------------------------------------------------------


@pytest.fixture
def entorno(fuente: FuenteFalsa, notificador: NotificadorRegistro, tmp_path: Path):
    repo = RepositorioMemoria()
    reloj = RelojCalendario(AHORA)
    servicio = ServicioVigilancia(
        fuente,
        repo,
        notificador,
        opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0),
        ahora=reloj,
        dormir=reloj.dormir,
        directorio_documentos=tmp_path / "docs",
    )
    return servicio, repo, fuente, notificador, reloj


def preparar_proceso_con_auto_nuevo(servicio: ServicioVigilancia, fuente: FuenteFalsa, reloj: RelojCalendario) -> Novedad:
    """Línea base con una actuación y luego un auto nuevo con documento adjunto."""
    fuente.registrar(hacer_proceso(fecha_ultima=date(2024, 5, 10)), [hacer_actuacion(1, 1, "Radicación", "", date(2024, 5, 10))])
    servicio.agregar(RADICADO, alias="Demo")
    servicio.verificar_todos()
    reloj.avanzar(days=1)
    auto = hacer_actuacion(2, 2, "Constancia secretarial", "AUTO ADMITE DEMANDA", date(2026, 9, 1), con_documentos=True)
    fuente.agregar_actuacion(ID_PROCESO, auto, nueva_fecha_ultima=date(2026, 9, 1))
    fuente.registrar_documento(hacer_documento(501, 2, "Auto admite demanda.pdf"))
    [resultado] = servicio.verificar_todos()
    [novedad_nueva] = resultado.novedades
    return novedad_nueva


class TestServicioDocumentos:
    def test_verificacion_adjunta_documentos_a_los_autos_nuevos(self, entorno):
        servicio, repo, fuente, notificador, reloj = entorno
        novedad_nueva = preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        assert [d.id_documento for d in novedad_nueva.documentos] == [501]
        assert ("documentos", 2) in fuente.llamadas
        assert repo.listar_documentos(2)[0].nombre == "Auto admite demanda.pdf"
        [evento] = notificador.eventos
        assert evento.autos[0].documentos[0].id_documento == 501
        texto = formatear_evento(evento)
        assert "Documento: Auto admite demanda.pdf -> " + url_descarga_documento(501) in texto

    def test_linea_base_no_pide_documentos(self, entorno):
        servicio, repo, fuente, _, _ = entorno
        fuente.registrar(hacer_proceso(), [hacer_actuacion(1, 1, "Constancia secretarial", "AUTO ADMITE", con_documentos=True)])
        servicio.agregar(RADICADO)
        servicio.verificar_todos()
        assert not any(l[0] == "documentos" for l in fuente.llamadas)

    def test_opcion_desactivada_no_pide_documentos(self, entorno):
        servicio, repo, fuente, _, reloj = entorno
        servicio.opciones.listar_documentos_de_autos = False
        novedad_nueva = preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        assert novedad_nueva.documentos == () and not any(l[0] == "documentos" for l in fuente.llamadas)

    def test_fallo_al_listar_no_frena_la_verificacion(self, entorno):
        servicio, repo, fuente, notificador, reloj = entorno
        fuente.error_documentos = ErrorFuente("HTTP 500", codigo=500)
        novedad_nueva = preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        assert novedad_nueva.es_auto and novedad_nueva.documentos == ()
        assert len(notificador.eventos) == 1

    def test_documentos_de_usa_cache_y_forzar_refresca(self, entorno):
        servicio, repo, fuente, _, reloj = entorno
        preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        llamadas_antes = fuente.llamadas.count(("documentos", 2))
        assert [d.id_documento for d in servicio.documentos_de(2)] == [501]
        assert fuente.llamadas.count(("documentos", 2)) == llamadas_antes, "se sirvió desde el repositorio"
        fuente.registrar_documento(hacer_documento(502, 2, "Anexo.pdf"))
        assert [d.id_documento for d in servicio.documentos_de(2, forzar=True)] == [501, 502]
        assert fuente.llamadas.count(("documentos", 2)) == llamadas_antes + 1

    def test_descarga_guarda_en_disco_y_reutiliza(self, entorno, tmp_path):
        servicio, repo, fuente, _, reloj = entorno
        preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        descarga = servicio.descargar_documento(501)
        assert descarga.contenido == PDF_MINIMO and descarga.tipo_contenido == "application/pdf"
        assert descarga.nombre == "Auto admite demanda.pdf"
        ruta = tmp_path / "docs" / "501.pdf"
        assert ruta.is_file() and repo.ruta_local_documento(501) == str(ruta)
        assert fuente.llamadas.count(("descarga", 501)) == 1
        de_nuevo = servicio.descargar_documento(501)
        assert de_nuevo.contenido == PDF_MINIMO
        assert fuente.llamadas.count(("descarga", 501)) == 1, "la segunda vez se lee del disco"

    def test_descarga_de_documento_desconocido(self, entorno):
        servicio, *_ = entorno
        with pytest.raises(DocumentoNoEncontrado):
            servicio.descargar_documento(999)

    def test_novedades_recientes_y_revisadas_via_servicio(self, entorno):
        servicio, repo, fuente, _, reloj = entorno
        novedad_nueva = preparar_proceso_con_auto_nuevo(servicio, fuente, reloj)
        recientes = servicio.novedades_recientes(solo_autos=True)
        assert [n.actuacion.id_registro for n in recientes] == [2]
        assert servicio.contar_pendientes() == 2
        assert servicio.marcar_revisada(novedad_nueva.actuacion.id_registro)
        assert servicio.contar_pendientes(solo_autos=True) == 0
        assert servicio.novedad(2).revisada is True
        assert servicio.novedades_recientes(radicado="11001 4003 001 2024 00123 45", solo_pendientes=True)[0].actuacion.id_registro == 1


# --- formato ------------------------------------------------------------------------------------------


def test_formato_incluye_documentos_y_enlaces():
    actuacion = hacer_actuacion(2, 2, "Constancia secretarial", "AUTO ADMITE", con_documentos=True)
    novedad_con_doc = Novedad(RADICADO, actuacion, True, ("AUTO",), DESPACHO, documentos=(hacer_documento(501, 2),))
    evento = EventoNovedades(RADICADO, "Demo", [hacer_proceso()], [novedad_con_doc], AHORA)
    texto = linea_novedad(novedad_con_doc)
    assert "[AUTO]" in texto and "Documento: Auto.pdf -> " in texto and "/Descarga/DocumentoActuacion/501" in texto
    datos = serializar_novedad(evento, novedad_con_doc)
    assert datos["documentos"][0]["url_fuente"].endswith("/501")
    assert datos["enlace_portal"].startswith("https://consultaprocesos.ramajudicial.gov.co/")
    assert "Portal: https://consultaprocesos.ramajudicial.gov.co" in formatear_evento(evento)
