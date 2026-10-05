"""Publicaciones procesales (micrositios): analizador HTML, patrones de radicado, PDF, cliente, repositorios y servicio."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import pytest
import respx

from apoyo import (
    DESPACHO,
    DESPACHO_CODIGO,
    ID_PROCESO,
    RADICADO,
    RADICADO_2,
    URL_PUB,
    FuenteFalsa,
    FuentePublicacionesFalsa,
    NotificadorRegistro,
    RelojCalendario,
    RelojFalso,
    hacer_actuacion,
    hacer_proceso,
    hacer_publicacion,
    pdf_con_texto,
)
from consultor_procesos.adaptadores.notificacion.formato import asunto_evento, formatear_evento, linea_publicacion
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.adaptadores.persistencia.sqlite import RepositorioSQLite
from consultor_procesos.adaptadores.publicaciones.analizador import analizar_lista, texto_plano
from consultor_procesos.adaptadores.publicaciones.cliente import ClientePublicaciones
from consultor_procesos.aplicacion.servicio_publicaciones import OpcionesPublicaciones, ServicioPublicaciones
from consultor_procesos.aplicacion.servicio_vigilancia import OpcionesVerificacion, ServicioVigilancia
from consultor_procesos.dominio.errores import ErrorFuente, FuenteNoDisponible
from consultor_procesos.dominio.modelos import CoincidenciaPublicacion, DetalleProceso, EstadoVerificacion, EventoNovedades, ProcesoVigilado
from consultor_procesos.dominio.reglas import buscar_radicado, normalizar_digitos, patrones_radicado
from consultor_procesos.enlaces import TIPOS_PUBLICACION
from consultor_procesos.infraestructura.cortesia import LimitadorTasa, PoliticaReintentos
from consultor_procesos.infraestructura.pdf import extraer_texto_pdf

AHORA = datetime(2026, 9, 2, 10, 0)
NS = "_co_com_avanti_efectosProcesales_PublicacionesEfectosProcesalesPortletV2_INSTANCE_BIyXQFHVaYaq_"
ESTADOS = TIPOS_PUBLICACION["Notificaciones por Estados"]
AVISOS = TIPOS_PUBLICACION["Notificaciones por Aviso"]


# --- fixture HTML con la estructura real del portal (datos ficticios) -----------------------------


def html_item(article_id, titulo, tipo, despacho_codigo, despacho, fecha, documentos, con_article_id=True):
    celdas = "".join(
        f'<td>{etiqueta}&nbsp; &nbsp; &nbsp;<a href="{url}" target="">VER</a></td>' for etiqueta, url in documentos
    )
    detalle = (
        f"https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio?p_p_id=x&amp;{NS}jspPage=%2FMETA-INF%2Fresources%2Fdetail.jsp"
        + (f"&amp;{NS}articleId={article_id}" if con_article_id else "")
        + f"&amp;{NS}nomDespacho={despacho.replace(' ', '+')}"
    )
    return f"""
<tr class=" col-xs-12 tramites "><td class="table-cell col-xs-12 traItem only" colspan="1"><div class="row"><div class="efecto">
<div class="titulo-publicacion fa fa-thin fa-hand-point-right"> <a href="{detalle}" title="Ver {titulo}" target="_self"> {titulo} </a> </div>
<div class="categorias-resumen"> <b>Categor&iacute;as | </b> <span class="categoria-ep">Tipo de publicaci&oacute;n:{tipo}</span>
<span class="categoria-ep">Departamento: BOGOT&Aacute;</span> <span class="categoria-ep">Municipio:BOGOT&Aacute; D.C.</span>
<span class="categoria-ep">Entidad:JUZGADO DE CIRCUITO </span> <span class="categoria-ep">Especialidad:CIVIL</span>
<span class="categoria-ep">Despacho:{despacho_codigo} - {despacho}</span> </div>
<div class="fecha-pub"> <p class="publish-date"> <i>Fecha de Publicaci&oacute;n:</i> {fecha}</p> </div>
<div class="row"> <!-- <div class="resumen-publicacion col-md-9 overflow-auto"> <p class="resume-ep"><table border="1"><tbody><tr>{celdas}</tr></tbody></table> <p>&nbsp;</p></p> </div> -->
<div class="boton-detalle col-md-12"> <a class="btn btn-round" href="{detalle}">Ver publicaci&oacute;n</a></div></div></div></div></td></tr>"""


def html_lista(items: list[str], resumen_total: str = "Mostrando 2 resultados.") -> str:
    return f"""<html><body><div id="{NS}publicacionesVOsSearchContainerSearchContainer"><small class="search-results"> {resumen_total} </small>
<table class="table"><tbody class="table-data">{''.join(items)}</tbody></table>
<ul class="lfr-pagination-buttons pager"><li>x</li></ul></div></body></html>"""


DOCS = [("ESTADO", "/documents/6098902/256565353/estado+82+del+31+de+ago+de+2026.pdf/5b89fc00?t=1"), ("AUTOS", "/documents/6098902/256565353/AUTOS+DEL+ESTADO+82.pdf/4a73b529?t=2")]
ITEM_1 = html_item("256567231", "Notificaci&oacute;n por Estado No.82 de 31 de agosto de 2026", "Notificaciones por Estados", DESPACHO_CODIGO, DESPACHO, "2026-08-31", DOCS)
ITEM_2 = html_item("256567999", f"Acci&oacute;n de Tutela Nro. {RADICADO}", "Acciones de Tutela", DESPACHO_CODIGO, DESPACHO, "2026-09-01", [], con_article_id=False)


class TestAnalizador:
    def test_extrae_los_campos_de_cada_publicacion(self):
        pagina = analizar_lista(html_lista([ITEM_1, ITEM_2]), id_estructura=ESTADOS, pagina=1, por_pagina=75)
        assert pagina.total == 2 and not pagina.hay_mas
        primera, segunda = pagina.publicaciones
        assert primera.id_publicacion == "256567231"
        assert primera.titulo == "Notificación por Estado No.82 de 31 de agosto de 2026"
        assert primera.tipo == "Notificaciones por Estados" and primera.id_estructura == ESTADOS
        assert primera.despacho_codigo == DESPACHO_CODIGO and primera.despacho == DESPACHO
        assert primera.fecha_publicacion == date(2026, 8, 31)
        assert primera.departamento == "BOGOTÁ" and primera.municipio == "BOGOTÁ D.C." and primera.especialidad == "CIVIL"
        assert primera.url_detalle.startswith("https://publicacionesprocesales.ramajudicial.gov.co/web/") and "articleId=256567231" in primera.url_detalle
        assert [d.etiqueta for d in primera.documentos] == ["ESTADO", "AUTOS"]
        assert primera.documentos[0].url == URL_PUB + "/documents/6098902/256565353/estado+82+del+31+de+ago+de+2026.pdf/5b89fc00?t=1"
        assert "ESTADO" in primera.resumen and "AUTOS" in primera.resumen
        assert segunda.id_publicacion.startswith("h"), "sin articleId se usa un hash estable"
        assert RADICADO in segunda.titulo and segunda.documentos == ()

    def test_total_con_intervalo_y_paginacion(self):
        pagina = analizar_lista(html_lista([ITEM_1], "Mostrando el intervalo 1 - 75 de 1.234 resultados."), pagina=1, por_pagina=75)
        assert pagina.total == 1234 and pagina.hay_mas
        assert analizar_lista(html_lista([ITEM_1], "Mostrando el intervalo 76 - 150 de 120 resultados."), pagina=2, por_pagina=75).hay_mas is False

    def test_pagina_sin_resultados(self):
        pagina = analizar_lista(html_lista([], "Mostrando 0 resultados."))
        assert pagina.publicaciones == () and pagina.total == 0

    def test_resultados_anunciados_sin_filas_reconocibles_falla_en_voz_alta(self):
        from consultor_procesos.dominio.errores import RespuestaInesperada

        html_cambiado = html_lista([ITEM_1]).replace("titulo-publicacion", "titulo-nuevo").replace("tramites", "fila")
        with pytest.raises(RespuestaInesperada, match="2 resultado"):
            analizar_lista(html_cambiado)

    def test_texto_plano(self):
        assert texto_plano("<p>Hola&nbsp;<b>mundo</b> &amp; m&aacute;s <!-- c --></p>") == "Hola mundo & más"


class TestPatronesRadicado:
    def test_normalizar_digitos(self):
        assert normalizar_digitos("11001-31-03-001-2020-00123-00") == "11001310300120200012300"
        assert normalizar_digitos("Rad. 2020 - 00123 y 2020.00124") == "Rad. 202000123 y 202000124"
        assert normalizar_digitos("el 12 de 2020") == "el 12 de 2020"

    def test_formas(self):
        formas = [f for f, _ in patrones_radicado(RADICADO)]
        assert formas == ["radicado_completo", "radicado_sin_instancia", "anio_consecutivo"]

    @pytest.mark.parametrize(
        "texto, forma",
        [
            (f"Radicado {RADICADO} auto admite", "radicado_completo"),
            ("Rad. 11001-40-03-001-2024-00123-45 se notifica", "radicado_completo"),
            ("Expediente 110014003001202400123 (sin instancia)", "radicado_sin_instancia"),
            ("Proceso 2024-00123 demandante X", "anio_consecutivo"),
            ("Proceso 2024 - 123 demandante X", "anio_consecutivo"),
        ],
    )
    def test_encuentra(self, texto, forma):
        resultado = buscar_radicado(texto, RADICADO)
        assert resultado is not None and resultado[0] == forma
        assert resultado[1]

    @pytest.mark.parametrize("texto", ["Proceso 2024-001234", "Radicado 11001400300120240012346", "2023-00123", "", None])
    def test_no_encuentra(self, texto):
        assert buscar_radicado(texto, RADICADO) is None


class TestPDF:
    def test_extrae_texto(self):
        texto = extraer_texto_pdf(pdf_con_texto(f"ESTADO No 82 Radicado {RADICADO} AUTO ADMITE"))
        assert RADICADO in normalizar_digitos(texto)

    def test_datos_invalidos(self):
        assert extraer_texto_pdf(b"no es un pdf") == ""
        assert extraer_texto_pdf(b"") == ""


@respx.mock
def test_cliente_construye_los_parametros_del_portal(reloj: RelojFalso):
    ruta = respx.get("https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio").mock(
        return_value=httpx.Response(200, text=html_lista([ITEM_1]))
    )
    descarga = respx.get(URL_PUB + "/documents/6098902/256565353/estado+82+del+31+de+ago+de+2026.pdf/5b89fc00").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 x", headers={"Content-Type": "application/pdf"})
    )
    with ClientePublicaciones(
        agente_usuario="PruebaUA/1.0",
        limitador=LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir),
        reintentos=PoliticaReintentos(intentos_max=2, espera_base=1.0, aleatorio=lambda: 1.0),
        dormir=reloj.dormir,
    ) as cliente:
        pagina = cliente.listar_publicaciones(DESPACHO_CODIGO, ESTADOS, date(2026, 8, 1), date(2026, 9, 2), pagina=2, por_pagina=50)
        contenido = cliente.descargar(pagina.publicaciones[0].documentos[0].url)
    assert len(pagina.publicaciones) == 1 and contenido.startswith(b"%PDF")
    parametros = ruta.calls.last.request.url.params
    assert parametros[NS + "idDespacho"] == DESPACHO_CODIGO
    assert parametros[NS + "idStructure"] == str(ESTADOS)
    assert parametros[NS + "fechaInicio"] == "2026-08-01" and parametros[NS + "fechaFin"] == "2026-09-02"
    assert parametros[NS + "action"] == "filterStructures" and parametros[NS + "cur"] == "2" and parametros[NS + "delta"] == "50"
    assert parametros["p_p_id"].endswith("_INSTANCE_BIyXQFHVaYaq")
    assert ruta.calls.last.request.headers["Accept"].startswith("text/html")
    assert ruta.calls.last.request.headers["User-Agent"] == "PruebaUA/1.0"
    assert descarga.called and cliente.solicitudes_realizadas == 2


# --- repositorios -----------------------------------------------------------------------------------


@pytest.fixture(params=["memoria", "sqlite"])
def repositorio(request, tmp_path):
    repo = RepositorioMemoria() if request.param == "memoria" else RepositorioSQLite(tmp_path / "p.sqlite")
    yield repo
    repo.cerrar()


class TestRepositorioPublicaciones:
    def test_publicaciones_ciclo(self, repositorio):
        p1 = hacer_publicacion("1", fecha=date(2026, 8, 30))
        p2 = hacer_publicacion("2", fecha=date(2026, 8, 31), tipo="Notificaciones por Aviso", id_estructura=AVISOS)
        assert repositorio.ids_publicaciones_conocidas(DESPACHO_CODIGO) == set()
        assert repositorio.guardar_publicaciones([p1, p2], AHORA) == 2
        assert repositorio.guardar_publicaciones([p1], AHORA) == 0, "idempotente"
        assert repositorio.ids_publicaciones_conocidas(DESPACHO_CODIGO) == {"1", "2"}
        listadas = repositorio.listar_publicaciones(despacho_codigo=DESPACHO_CODIGO)
        assert [p.id_publicacion for p in listadas] == ["2", "1"], "más recientes primero"
        guardada = repositorio.obtener_publicacion("1")
        assert guardada.titulo == p1.titulo and guardada.visto_en == AHORA and guardada.documentos == p1.documentos
        assert guardada.despacho == DESPACHO and guardada.municipio == "BOGOTÁ D.C."
        assert repositorio.obtener_publicacion("no") is None
        assert repositorio.listar_publicaciones(despacho_codigo="000000000000") == []

    def test_coincidencias_y_revisiones(self, repositorio):
        publicacion = hacer_publicacion("1")
        repositorio.guardar_publicaciones([publicacion], AHORA)
        c1 = CoincidenciaPublicacion(publicacion, RADICADO, "radicado_completo", "titulo o resumen", "frag")
        c2 = CoincidenciaPublicacion(publicacion, RADICADO_2, "anio_consecutivo", "documento: AUTOS", "frag2")
        assert repositorio.guardar_coincidencias([c1, c2], AHORA) == 2
        assert repositorio.guardar_coincidencias([c1], AHORA) == 0, "misma publicación, radicado y lugar"
        assert repositorio.contar_coincidencias_pendientes() == 2
        todas = repositorio.listar_coincidencias()
        assert {c.radicado for c in todas} == {RADICADO, RADICADO_2}
        assert all(c.id is not None and c.visto_en == AHORA and c.publicacion.titulo == publicacion.titulo for c in todas)
        [propia] = repositorio.listar_coincidencias(radicado=RADICADO)
        assert propia.donde == "titulo o resumen" and propia.fragmento == "frag"
        assert repositorio.marcar_coincidencia_revisada(propia.id)
        assert repositorio.contar_coincidencias_pendientes() == 1
        assert [c.radicado for c in repositorio.listar_coincidencias(solo_pendientes=True)] == [RADICADO_2]
        assert not repositorio.marcar_coincidencia_revisada(9999)
        assert repositorio.obtener_revision_despacho(DESPACHO_CODIGO) is None
        repositorio.registrar_revision_despacho(DESPACHO_CODIGO, AHORA)
        repositorio.registrar_revision_despacho(DESPACHO_CODIGO, AHORA + timedelta(hours=1))
        assert repositorio.obtener_revision_despacho(DESPACHO_CODIGO) == AHORA + timedelta(hours=1)

    def test_eliminar_vigilado_borra_sus_coincidencias(self, repositorio):
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO, despachos=("050002204000",)))
        assert repositorio.obtener_vigilado(RADICADO).despachos == ("050002204000",)
        assert repositorio.obtener_vigilado(RADICADO).codigos_despacho == (DESPACHO_CODIGO, "050002204000")
        publicacion = hacer_publicacion("1")
        repositorio.guardar_publicaciones([publicacion], AHORA)
        repositorio.guardar_coincidencias([CoincidenciaPublicacion(publicacion, RADICADO, "x", "y")], AHORA)
        repositorio.eliminar_vigilado(RADICADO)
        assert repositorio.listar_coincidencias() == []
        assert repositorio.obtener_publicacion("1") is not None, "la publicación del despacho se conserva"


# --- servicio -----------------------------------------------------------------------------------------


@pytest.fixture
def entorno(notificador: NotificadorRegistro):
    fuente = FuentePublicacionesFalsa()
    repo = RepositorioMemoria()
    reloj = RelojCalendario(AHORA)
    opciones = OpcionesPublicaciones(tipos=(ESTADOS, AVISOS), horas_entre_revisiones=20, dias_ventana_inicial=7, pausa_entre_despachos_segundos=2.0)
    servicio = ServicioPublicaciones(fuente, repo, notificador, opciones=opciones, ahora=reloj, dormir=reloj.dormir)
    vigilado = ProcesoVigilado(radicado=RADICADO, alias="Demo", inicializado=True)
    return servicio, fuente, repo, notificador, reloj, vigilado


class TestServicioPublicaciones:
    def test_primera_revision_encuentra_en_el_titulo_y_notifica(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.registrar(hacer_publicacion("1", titulo=f"Acción de Tutela Nro. {RADICADO}", tipo="Notificaciones por Aviso", id_estructura=AVISOS, documentos=()))
        fuente.registrar(hacer_publicacion("2", titulo="Estado No. 80", documentos=()))

        [resultado] = servicio.revisar([vigilado])

        assert resultado.estado == EstadoVerificacion.OK and resultado.nuevas == 2 and resultado.despacho == DESPACHO
        [coincidencia] = resultado.coincidencias
        assert coincidencia.radicado == RADICADO and coincidencia.forma == "radicado_completo" and coincidencia.donde == "titulo o resumen"
        assert resultado.solicitudes == 3, "una consulta por tipo configurado y el detalle del estado sin documentos"
        assert ("detalle", hacer_publicacion("2").url_detalle) in fuente.llamadas
        assert repo.ids_publicaciones_conocidas(DESPACHO_CODIGO) == {"1", "2"}
        assert repo.obtener_revision_despacho(DESPACHO_CODIGO) == AHORA
        [evento] = notificador.eventos
        assert evento.radicado == RADICADO and evento.alias == "Demo" and evento.novedades == [] and len(evento.publicaciones) == 1
        assert "[PUBLICACION]" in formatear_evento(evento) and "publicación(es) del despacho mencionan" in asunto_evento(evento)
        llamada = fuente.llamadas[0]
        assert llamada[3] == AHORA.date() - timedelta(days=7) and llamada[4] == AHORA.date()

    def test_busca_dentro_del_pdf_de_los_autos(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        publicacion = hacer_publicacion("1")
        fuente.registrar(
            publicacion,
            {publicacion.documentos[0].url: pdf_con_texto("ESTADO 82: procesos 2024-00999 y 2023-00123"), publicacion.documentos[1].url: pdf_con_texto("AUTO ADMITE DEMANDA rad 2024-00123 demandante X")},
        )

        [resultado] = servicio.revisar([vigilado])

        [coincidencia] = resultado.coincidencias
        assert coincidencia.donde == "documento: AUTOS" and coincidencia.forma == "anio_consecutivo"
        assert "202400123" in coincidencia.fragmento
        assert repo.obtener_publicacion("1").analizada is True
        assert [l for l in fuente.llamadas if l[0] == "descargar"] == [("descargar", publicacion.documentos[0].url), ("descargar", publicacion.documentos[1].url)]
        assert linea_publicacion(coincidencia).count("Documento:") == 2

    def test_sin_analisis_de_pdf_no_descarga(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        servicio.opciones.analizar_pdf = False
        fuente.registrar(hacer_publicacion("1"), {hacer_publicacion("1").documentos[1].url: pdf_con_texto(RADICADO)})
        [resultado] = servicio.revisar([vigilado])
        assert resultado.coincidencias == [] and not any(l[0] == "descargar" for l in fuente.llamadas)
        assert repo.obtener_publicacion("1").analizada is False

    def test_pdf_escaneado_o_fallido_no_frena(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        publicacion = hacer_publicacion("1")
        fuente.registrar(publicacion, {publicacion.documentos[0].url: b"%PDF-1.4 sin texto"})  # el segundo documento no existe -> 404
        [resultado] = servicio.revisar([vigilado])
        assert resultado.estado == EstadoVerificacion.OK and resultado.coincidencias == []
        assert repo.obtener_publicacion("1").analizada is False

    def test_no_repite_dentro_del_intervalo_y_forzar_lo_permite(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.registrar(hacer_publicacion("1", documentos=()))
        servicio.revisar([vigilado])
        reloj.avanzar(hours=5)
        [resultado] = servicio.revisar([vigilado])
        assert resultado.estado == EstadoVerificacion.SIN_CAMBIOS and resultado.solicitudes == 0
        fuente.registrar(hacer_publicacion("2", titulo=f"Traslado {RADICADO}", fecha=AHORA.date(), documentos=()))
        [forzado] = servicio.revisar([vigilado], forzar=True)
        assert forzado.estado == EstadoVerificacion.OK and forzado.nuevas == 1 and len(forzado.coincidencias) == 1
        llamada = [l for l in fuente.llamadas if l[0] == "listar"][-1]
        assert llamada[3] == AHORA.date() - timedelta(days=1), "ventana desde la última revisión menos un día de solapamiento"
        reloj.avanzar(hours=20)
        assert servicio.revisar([vigilado])[0].estado == EstadoVerificacion.OK

    def test_varios_radicados_del_mismo_despacho_en_una_sola_consulta(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        otro = ProcesoVigilado(radicado=DESPACHO_CODIGO + "20230000100", alias="Otro")
        fuente.registrar(hacer_publicacion("1", titulo=f"Traslado {RADICADO} y {otro.radicado}", documentos=()))
        [resultado] = servicio.revisar([vigilado, otro])
        assert {c.radicado for c in resultado.coincidencias} == {RADICADO, otro.radicado}
        assert resultado.solicitudes == 2 and len(notificador.eventos) == 2

    def test_publicaciones_de_otro_despacho_se_descartan(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        ajena = hacer_publicacion("9", despacho_codigo="050002204000", titulo=RADICADO, documentos=())
        fuente.publicaciones[(DESPACHO_CODIGO, ESTADOS)] = [ajena]
        [resultado] = servicio.revisar([vigilado])
        assert resultado.nuevas == 0 and resultado.coincidencias == []

    def test_error_definitivo_devuelve_error_y_fuente_caida_omite_el_resto(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.error_listar = ErrorFuente("HTTP 400", codigo=400)
        [resultado] = servicio.revisar([vigilado])
        assert resultado.estado == EstadoVerificacion.ERROR and "400" in resultado.mensaje
        assert repo.obtener_revision_despacho(DESPACHO_CODIGO) is None

        fuente.error_listar = FuenteNoDisponible("caída")
        otro = ProcesoVigilado(radicado="05000220400020240000100")
        resultados = servicio.revisar([vigilado, otro])
        assert [r.estado for r in resultados] == [EstadoVerificacion.OMITIDO, EstadoVerificacion.OMITIDO]
        assert resultados[1].mensaje.startswith("Lote de publicaciones detenido")
        assert reloj.esperas == [], "no se pausa cuando ya se decidió detener"

    def test_despachos_de_incluye_los_descubiertos(self):
        v1 = ProcesoVigilado(radicado=RADICADO, despachos=("050002204000",))
        v2 = ProcesoVigilado(radicado=DESPACHO_CODIGO + "20230000100")
        grupos = ServicioPublicaciones.despachos_de([v1, v2])
        assert list(grupos) == ["050002204000", DESPACHO_CODIGO]
        assert [v.radicado for v in grupos[DESPACHO_CODIGO]] == [RADICADO, v2.radicado]

    def test_consultas_locales(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.registrar(hacer_publicacion("1", titulo=RADICADO, documentos=()))
        servicio.revisar([vigilado])
        [coincidencia] = servicio.coincidencias_recientes(radicado=RADICADO)
        assert servicio.contar_pendientes() == 1
        assert servicio.marcar_revisada(coincidencia.id)
        assert servicio.contar_pendientes() == 0 and servicio.coincidencias_recientes(solo_pendientes=True) == []
        assert [p.id_publicacion for p in servicio.publicaciones_despacho(DESPACHO_CODIGO)] == ["1"]


class TestIntegracionConVigilancia:
    def test_verificar_todos_revisa_publicaciones_y_descubre_despachos(self, fuente: FuenteFalsa, notificador: NotificadorRegistro):
        repo = RepositorioMemoria()
        reloj = RelojCalendario(AHORA)
        publicaciones_falsas = FuentePublicacionesFalsa()
        servicio_pub = ServicioPublicaciones(
            publicaciones_falsas, repo, notificador, opciones=OpcionesPublicaciones(tipos=(ESTADOS,), pausa_entre_despachos_segundos=0), ahora=reloj
        )
        servicio = ServicioVigilancia(
            fuente, repo, notificador, opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0), ahora=reloj, dormir=reloj.dormir, publicaciones=servicio_pub
        )
        fuente.registrar(hacer_proceso(fecha_ultima=date(2024, 5, 10)), [hacer_actuacion(1, 1, "Radicación", "", date(2024, 5, 10))])
        fuente.detalles[ID_PROCESO] = DetalleProceso(id_proceso=ID_PROCESO, radicado=RADICADO, codigo_despacho="050002204000")
        publicaciones_falsas.registrar(hacer_publicacion("1", titulo=f"Estado {RADICADO}", documentos=()))
        publicaciones_falsas.registrar(hacer_publicacion("2", despacho_codigo="050002204000", titulo=f"Traslado {RADICADO}", documentos=()))
        servicio.agregar(RADICADO, alias="Demo")

        [resultado] = servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and resultado.es_linea_base
        assert repo.obtener_vigilado(RADICADO).despachos == ("050002204000",)
        assert [r.despacho_codigo for r in servicio.ultimos_resultados_publicaciones] == ["050002204000", DESPACHO_CODIGO]
        assert len(resultado.publicaciones) == 2
        assert len(notificador.eventos) == 2, "dos avisos de publicaciones (uno por despacho); la línea base no avisa"
        assert resultado.solicitudes == 3, "búsqueda, actuaciones y detalle para descubrir el despacho"

    def test_sin_servicio_de_publicaciones_no_cambia_nada(self, fuente: FuenteFalsa, notificador: NotificadorRegistro):
        repo = RepositorioMemoria()
        servicio = ServicioVigilancia(fuente, repo, notificador, opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0, descubrir_despachos=False), ahora=lambda: AHORA, dormir=lambda s: None)
        fuente.registrar(hacer_proceso(), [hacer_actuacion(1, 1)])
        servicio.agregar(RADICADO)
        [resultado] = servicio.verificar_todos()
        assert resultado.publicaciones == [] and servicio.ultimos_resultados_publicaciones == [] and resultado.solicitudes == 2


@respx.mock
def test_si_la_ruta_inicio_da_404_usa_la_raiz_y_se_queda_con_ella(reloj: RelojFalso):
    """El 4 oct 2026 /web/publicaciones-procesales/inicio respondía 404 y la raíz servía la misma consulta."""
    inicio = respx.get("https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio").mock(
        return_value=httpx.Response(404, text="<!doctype html><html><head><title>Estado HTTP 404 – No encontrado</title><style>h1{}</style>")
    )
    raiz = respx.get("https://publicacionesprocesales.ramajudicial.gov.co/").mock(
        return_value=httpx.Response(200, text=html_lista([ITEM_1]))
    )
    with ClientePublicaciones(
        agente_usuario="PruebaUA/1.0",
        limitador=LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir),
        reintentos=PoliticaReintentos(intentos_max=1),
        dormir=reloj.dormir,
    ) as cliente:
        primera = cliente.listar_publicaciones(DESPACHO_CODIGO, ESTADOS, date(2026, 9, 1), date(2026, 9, 2))
        segunda = cliente.listar_publicaciones(DESPACHO_CODIGO, ESTADOS, date(2026, 9, 1), date(2026, 9, 2))
    assert len(primera.publicaciones) == 1 and len(segunda.publicaciones) == 1
    assert inicio.call_count == 1 and raiz.call_count == 2, "tras el 404 se sigue usando la raíz"
    assert raiz.calls.last.request.url.params[NS + "idDespacho"] == DESPACHO_CODIGO


@respx.mock
def test_si_ninguna_ruta_responde_el_error_es_legible(reloj: RelojFalso):
    pagina_404 = "<!doctype html><html lang='es'><head><title>Estado HTTP 404 – No encontrado</title><style>h1 {font-family:Tahoma}</style>"
    respx.get("https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio").mock(
        return_value=httpx.Response(404, text=pagina_404, headers={"Content-Type": "text/html"})
    )
    respx.get("https://publicacionesprocesales.ramajudicial.gov.co/").mock(
        return_value=httpx.Response(404, text=pagina_404, headers={"Content-Type": "text/html"})
    )
    with ClientePublicaciones(
        agente_usuario="PruebaUA/1.0",
        limitador=LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir),
        reintentos=PoliticaReintentos(intentos_max=1),
        dormir=reloj.dormir,
    ) as cliente:
        with pytest.raises(ErrorFuente) as info:
            cliente.listar_publicaciones(DESPACHO_CODIGO, ESTADOS, date(2026, 9, 1), date(2026, 9, 2))
    assert str(info.value) == "HTTP 404: Estado HTTP 404 – No encontrado"


# --- detalle de la publicación (despachos que publican un PDF por auto) ----------------------------

HTML_DETALLE = """<html><body>
<a href="/documents/20135/1/infografia+publicaciones.pdf/cea8">Ver Instructivo</a>
<div class="detalle-publicacion-ep container-fluid"><h2>Notificación por Estado No. 091 de 01 de octubre de 2026</h2>
<div class="datosTitle"><b>Número de Radicación</b></div><ul><li>091</li></ul>
<table id="tabla-docs-1-0"><tbody>
<tr><td><a href="/c/document_library/get_file?uuid=aaaa&amp;groupId=6098902" target="_blank"> 2021-01203 RESUELVE RECURSO.pdf </a></td><td>01-oct-2026</td></tr>
<tr><td><a href="/c/document_library/get_file?uuid=bbbb&amp;groupId=6098902" target="_blank"> 2024-00123 NO TIENE EN CUENTA NOTIF.pdf </a></td><td>01-oct-2026</td></tr>
<tr><td><a href="/c/document_library/get_file?uuid=cccc&amp;groupId=6098902" target="_blank"> planilla estado 091.pdf </a></td><td>01-oct-2026</td></tr>
</tbody></table></div>
<footer><a href="/documents/20135/1/ABC.pdf/e52a">Ver ABC</a></footer></body></html>"""


class TestDetalleDePublicacion:
    def test_analizador_toma_solo_los_documentos_de_la_publicacion(self):
        from consultor_procesos.adaptadores.publicaciones.analizador import analizar_detalle

        detalle = analizar_detalle(HTML_DETALLE)
        assert [d.etiqueta for d in detalle.documentos] == [
            "2021-01203 RESUELVE RECURSO.pdf",
            "2024-00123 NO TIENE EN CUENTA NOTIF.pdf",
            "planilla estado 091.pdf",
        ], "sin el instructivo ni el ABC del portal"
        assert detalle.documentos[1].url == URL_PUB + "/c/document_library/get_file?uuid=bbbb&groupId=6098902"
        assert "Estado No. 091" in detalle.texto

    def test_detalle_con_estructura_desconocida_falla_en_voz_alta(self):
        from consultor_procesos.adaptadores.publicaciones.analizador import analizar_detalle
        from consultor_procesos.dominio.errores import RespuestaInesperada

        with pytest.raises(RespuestaInesperada, match="detalle-publicacion-ep"):
            analizar_detalle("<html><body><div class='otra-cosa'></div></body></html>")

    def test_encuentra_el_radicado_en_el_nombre_del_pdf_del_detalle(self, entorno):
        """Caso real (4 oct 2026, Juzgado 036 de Pequeñas Causas de Bogotá): el listado no trae resumen ni
        documentos y el auto aparece en el detalle como '2025-00451 NO TIENE EN CUENTA NOTIF.pdf'."""
        from consultor_procesos.adaptadores.publicaciones.analizador import analizar_detalle

        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        publicacion = hacer_publicacion("91", titulo="Notificación por Estado No.091 de 01 de octubre de 2026", resumen="", documentos=())
        fuente.registrar(publicacion)
        fuente.detalles[publicacion.url_detalle] = analizar_detalle(HTML_DETALLE)

        [resultado] = servicio.revisar([vigilado])

        [coincidencia] = resultado.coincidencias
        assert coincidencia.forma == "anio_consecutivo" and coincidencia.donde == "documento: 2024-00123 NO TIENE EN CUENTA NOTIF.pdf"
        assert coincidencia.fragmento == "2024-00123 NO TIENE EN CUENTA NOTIF.pdf"
        assert [d.etiqueta for d in coincidencia.publicacion.documentos] == [
            "2024-00123 NO TIENE EN CUENTA NOTIF.pdf",
            "planilla estado 091.pdf",
        ], "se guardan el documento del proceso y la planilla, no los autos de otros procesos"
        assert not any(l[0] == "descargar" for l in fuente.llamadas), "el nombre bastó: no se descargó ningún PDF"
        [guardada] = repo.listar_publicaciones(DESPACHO_CODIGO)
        assert guardada.analizada and len(guardada.documentos) == 2
        [evento] = notificador.eventos
        assert evento.publicaciones[0].donde.startswith("documento: 2024-00123")

    def test_si_ningun_nombre_lo_menciona_lee_primero_la_planilla(self, entorno):
        from consultor_procesos.dominio.modelos import DetallePublicacion, DocumentoPublicado

        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        publicacion = hacer_publicacion("92", resumen="", documentos=())
        auto = DocumentoPublicado("Auto 1.pdf", URL_PUB + "/c/document_library/get_file?uuid=1")
        planilla = DocumentoPublicado("planilla estado 092.pdf", URL_PUB + "/c/document_library/get_file?uuid=2")
        fuente.registrar(publicacion, {planilla.url: pdf_con_texto(f"ESTADO 092 Radicado {RADICADO} AUTO REQUIERE")})
        fuente.detalles[publicacion.url_detalle] = DetallePublicacion(texto="Estado 092", documentos=(auto, planilla))

        [resultado] = servicio.revisar([vigilado])

        [coincidencia] = resultado.coincidencias
        assert coincidencia.donde == "documento: planilla estado 092.pdf"
        descargas = [l[1] for l in fuente.llamadas if l[0] == "descargar"]
        assert descargas == [planilla.url], "la planilla primero; encontrado ahí, no se abren más PDF"

    def test_detalle_que_no_responde_no_frena_la_revision(self, entorno):
        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.registrar(hacer_publicacion("93", resumen="", documentos=()))
        [resultado] = servicio.revisar([vigilado])
        assert resultado.estado == EstadoVerificacion.OK and resultado.coincidencias == []

    def test_cambio_de_estructura_del_detalle_deja_el_despacho_en_error(self, entorno):
        from consultor_procesos.dominio.errores import RespuestaInesperada

        servicio, fuente, repo, notificador, reloj, vigilado = entorno
        fuente.registrar(hacer_publicacion("94", resumen="", documentos=()))
        fuente.error_detalle = RespuestaInesperada("La página de detalle de la publicación no tiene la sección")
        [resultado] = servicio.revisar([vigilado])
        assert resultado.estado == EstadoVerificacion.ERROR and "detalle" in resultado.mensaje
        assert repo.obtener_revision_despacho(DESPACHO_CODIGO) is None, "se reintentará en la próxima revisión"


@respx.mock
def test_cliente_obtiene_el_detalle_y_usa_la_raiz_si_inicio_da_404(reloj: RelojFalso):
    inicio = respx.get("https://publicacionesprocesales.ramajudicial.gov.co/web/publicaciones-procesales/inicio").mock(
        return_value=httpx.Response(404, text="<html><title>404</title></html>")
    )
    raiz = respx.get("https://publicacionesprocesales.ramajudicial.gov.co/").mock(return_value=httpx.Response(200, text=HTML_DETALLE))
    with ClientePublicaciones(
        agente_usuario="PruebaUA/1.0",
        limitador=LimitadorTasa(6000, rafaga=100, reloj=reloj, dormir=reloj.dormir),
        reintentos=PoliticaReintentos(intentos_max=1),
        dormir=reloj.dormir,
    ) as cliente:
        detalle = cliente.obtener_detalle(URL_PUB + "/web/publicaciones-procesales/inicio?articleId=91&p_p_id=x")
    assert len(detalle.documentos) == 3 and inicio.call_count == 1
    assert raiz.calls.last.request.url.params["articleId"] == "91"


@pytest.mark.parametrize(
    "etiqueta, general",
    [
        ("planilla estado 091.pdf", True),
        ("ESTADO 82.pdf", True),
        ("TRASLADO No. 011 - 30 SEPTIEMBRE.pdf", True),
        ("2022-00850Auto Corre Traslado.pdf", False),
        ("2021-01203 AutoRemitirCorrerTraslado.pdf", False),
        ("11001418903620250045100 aviso.pdf", False),
        ("Auto 1.pdf", False),
    ],
)
def test_documentos_generales_frente_a_autos_de_un_proceso(etiqueta, general):
    from consultor_procesos.aplicacion.servicio_publicaciones import _es_documento_general
    from consultor_procesos.dominio.modelos import DocumentoPublicado

    assert _es_documento_general(DocumentoPublicado(etiqueta, "https://x")) is general
