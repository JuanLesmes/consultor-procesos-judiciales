"""Pruebas del caso de uso principal con fuente falsa, repositorio en memoria y notificador de registro."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from apoyo import (
    DESPACHO,
    ID_PROCESO,
    ID_PROCESO_2,
    RADICADO,
    RADICADO_2,
    FuenteFalsa,
    NotificadorRegistro,
    RelojCalendario,
    hacer_actuacion,
    hacer_proceso,
)
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.aplicacion.servicio_vigilancia import (
    OpcionesVerificacion,
    ServicioVigilancia,
    recorrer_actuaciones,
)
from consultor_procesos.dominio.errores import (
    ErrorFuente,
    FuenteNoDisponible,
    PresupuestoAgotado,
    ProcesoNoEncontrado,
    RadicadoInvalido,
)
from consultor_procesos.dominio.modelos import EstadoVerificacion

AHORA = datetime(2026, 9, 2, 10, 0)


@pytest.fixture
def entorno(fuente: FuenteFalsa, notificador: NotificadorRegistro):
    repo = RepositorioMemoria()
    reloj = RelojCalendario(AHORA)
    opciones = OpcionesVerificacion(
        dias_gracia=5,
        horas_refresco_completo=24,
        max_paginas_inicial=5,
        pausa_entre_procesos_segundos=2.0,
        descubrir_despachos=False,
    )
    servicio = ServicioVigilancia(fuente, repo, notificador, opciones=opciones, ahora=reloj, dormir=reloj.dormir)
    return SimpleNamespace(servicio=servicio, repo=repo, fuente=fuente, notificador=notificador, reloj=reloj)


def actuaciones_base(fecha_ultima: date, id_proceso: int = ID_PROCESO, radicado: str = RADICADO):
    return [
        hacer_actuacion(1001, 1, "Radicación de proceso", "", date(2024, 4, 1), radicado, id_proceso),
        hacer_actuacion(1002, 2, "Constancia secretarial", "AUTO ADMITE DEMANDA", date(2024, 4, 15), radicado, id_proceso),
        hacer_actuacion(1003, 3, "Envío comunicaciones", "SE COMUNICA A LAS PARTES", fecha_ultima, radicado, id_proceso),
    ]


def registrar_base(fuente: FuenteFalsa, fecha_ultima: date = date(2024, 5, 10)) -> None:
    fuente.registrar(hacer_proceso(fecha_ultima=fecha_ultima), actuaciones_base(fecha_ultima))


class TestGestion:
    def test_agregar_normaliza_y_persiste(self, entorno):
        vigilado = entorno.servicio.agregar("11001 4003 001 2024 00123 45", alias="Demo")
        assert vigilado.radicado == RADICADO and vigilado.alias == "Demo" and vigilado.creado_en == AHORA
        assert [v.radicado for v in entorno.servicio.listar()] == [RADICADO]

    def test_agregar_duplicado_reactiva_y_actualiza_alias(self, entorno):
        entorno.servicio.agregar(RADICADO, alias="viejo")
        vigilado = entorno.repo.obtener_vigilado(RADICADO)
        vigilado.activo = False
        entorno.repo.guardar_vigilado(vigilado)
        de_nuevo = entorno.servicio.agregar(RADICADO, alias="nuevo")
        assert de_nuevo.activo and de_nuevo.alias == "nuevo"
        assert len(entorno.servicio.listar(solo_activos=False)) == 1

    def test_agregar_invalido(self, entorno):
        with pytest.raises(RadicadoInvalido):
            entorno.servicio.agregar("abc")

    def test_quitar(self, entorno):
        entorno.servicio.agregar(RADICADO)
        assert entorno.servicio.quitar(RADICADO)
        assert not entorno.servicio.quitar(RADICADO)


class TestLineaBase:
    def test_primera_verificacion_registra_sin_notificar(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and resultado.es_linea_base
        assert len(resultado.novedades) == 3 and len(resultado.autos) == 1
        assert resultado.autos[0].actuacion.id_registro == 1002
        assert resultado.solicitudes == 2, "una búsqueda y una página de actuaciones"
        assert entorno.notificador.eventos == [], "la línea base no se notifica por defecto"
        assert entorno.repo.ids_actuaciones_conocidas(RADICADO) == {1001, 1002, 1003}
        vigilado = entorno.repo.obtener_vigilado(RADICADO)
        assert vigilado.inicializado and vigilado.id_proceso == ID_PROCESO
        assert vigilado.fecha_ultima_actuacion == date(2024, 5, 10)
        assert vigilado.huella == f"{ID_PROCESO}:2024-05-10"
        assert vigilado.ultima_lectura_actuaciones == AHORA
        assert len(entorno.repo.listar_verificaciones()) == 1

    def test_linea_base_se_notifica_si_se_pide(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.opciones.notificar_existentes = True
        entorno.servicio.agregar(RADICADO, alias="Demo")
        entorno.servicio.verificar_todos()
        [evento] = entorno.notificador.eventos
        assert evento.es_linea_base and evento.alias == "Demo" and len(evento.autos) == 1

    def test_autos_registrados(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        autos = entorno.servicio.autos_registrados(RADICADO)
        assert [n.actuacion.id_registro for n in autos] == [1002]
        assert len(entorno.servicio.actuaciones_registradas(RADICADO)) == 3


class TestVerificacionesPosteriores:
    def test_sin_cambios_usa_una_sola_solicitud(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(hours=4)

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.SIN_CAMBIOS
        assert resultado.solicitudes == 1
        assert resultado.novedades == []

    def test_una_ultima_actuacion_reciente_obliga_a_releer(self, entorno):
        registrar_base(entorno.fuente, fecha_ultima=AHORA.date())
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(hours=1)

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and not resultado.es_linea_base
        assert resultado.novedades == [] and resultado.solicitudes == 2
        assert entorno.notificador.eventos == []

    def test_refresco_completo_tras_24_horas(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(hours=25)

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and resultado.solicitudes == 2

    def test_nueva_actuacion_detecta_auto_notifica_y_se_detiene_en_lo_conocido(self, entorno):
        entorno.fuente.por_pagina = 2
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO, alias="Demo")
        entorno.servicio.verificar_todos()
        assert ("actuaciones", ID_PROCESO, 2) in entorno.fuente.llamadas, "la línea base leyó dos páginas"
        entorno.fuente.llamadas.clear()
        entorno.reloj.avanzar(days=1)

        nueva = hacer_actuacion(1004, 4, "Constancia secretarial", "AUTO FIJA FECHA PARA AUDIENCIA", date(2026, 9, 1))
        entorno.fuente.agregar_actuacion(ID_PROCESO, nueva, nueva_fecha_ultima=date(2026, 9, 1))

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and not resultado.es_linea_base
        assert [n.actuacion.id_registro for n in resultado.novedades] == [1004]
        assert resultado.autos[0].coincidencias == ("AUTO",)
        assert resultado.autos[0].despacho == entorno.fuente.procesos[RADICADO][0].despacho
        assert [l for l in entorno.fuente.llamadas if l[0] == "actuaciones"] == [("actuaciones", ID_PROCESO, 1)], (
            "se detuvo en la primera actuación conocida sin pedir la página 2"
        )
        [evento] = entorno.notificador.eventos
        assert not evento.es_linea_base and evento.alias == "Demo" and len(evento.autos) == 1
        assert 1004 in entorno.repo.ids_actuaciones_conocidas(RADICADO)
        assert entorno.repo.obtener_vigilado(RADICADO).fecha_ultima_actuacion == date(2026, 9, 1)

    def test_no_vuelve_a_notificar_lo_ya_visto(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        nueva = hacer_actuacion(1004, 4, anotacion="AUTO DECRETA PRUEBAS", fecha=date(2026, 9, 1))
        entorno.fuente.agregar_actuacion(ID_PROCESO, nueva, nueva_fecha_ultima=date(2026, 9, 1))
        entorno.servicio.verificar_todos()
        assert len(entorno.notificador.eventos) == 1

        entorno.reloj.avanzar(hours=2)
        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK, "fecha reciente: se releen actuaciones"
        assert resultado.novedades == []
        assert len(entorno.notificador.eventos) == 1

    def test_mismo_radicado_en_dos_despachos(self, entorno):
        registrar_base(entorno.fuente)
        tribunal = hacer_proceso(id_proceso=ID_PROCESO_2, fecha_ultima=date(2025, 2, 1), despacho="TRIBUNAL SUPERIOR DE PRUEBA")
        entorno.fuente.registrar(
            tribunal,
            [hacer_actuacion(2001, 1, "Constancia secretarial", "AUTO ADMITE RECURSO DE APELACIÓN", date(2025, 2, 1), id_proceso=ID_PROCESO_2)],
        )
        entorno.servicio.agregar(RADICADO)

        [resultado] = entorno.servicio.verificar_todos()

        assert len(resultado.procesos) == 2
        assert len(resultado.novedades) == 4 and len(resultado.autos) == 2
        assert {n.despacho for n in resultado.autos} == {DESPACHO, "TRIBUNAL SUPERIOR DE PRUEBA"}
        vigilado = entorno.repo.obtener_vigilado(RADICADO)
        assert vigilado.huella == f"{ID_PROCESO}:2024-05-10;{ID_PROCESO_2}:2025-02-01"
        assert vigilado.fecha_ultima_actuacion == date(2025, 2, 1)
        assert resultado.solicitudes == 3


class TestEstadosEspeciales:
    def test_no_encontrado(self, entorno):
        entorno.servicio.agregar(RADICADO)
        [resultado] = entorno.servicio.verificar_todos()
        assert resultado.estado == EstadoVerificacion.NO_ENCONTRADO
        assert entorno.repo.obtener_vigilado(RADICADO).ultima_verificacion == AHORA
        assert entorno.repo.listar_verificaciones()[0]["estado"] == "NO_ENCONTRADO"

    def test_privado_no_descarga_actuaciones(self, entorno):
        entorno.fuente.registrar(hacer_proceso(es_privado=True), actuaciones_base(date(2024, 5, 10)))
        entorno.servicio.agregar(RADICADO)
        [resultado] = entorno.servicio.verificar_todos()
        assert resultado.estado == EstadoVerificacion.PRIVADO and resultado.solicitudes == 1
        assert entorno.repo.obtener_vigilado(RADICADO).id_proceso == ID_PROCESO

    def test_error_definitivo_de_la_fuente_no_detiene_el_lote(self, entorno):
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.agregar(RADICADO_2)
        entorno.fuente.error_busqueda = ErrorFuente("HTTP 400: parámetro inválido", codigo=400)
        resultados = entorno.servicio.verificar_todos()
        assert [r.estado for r in resultados] == [EstadoVerificacion.ERROR, EstadoVerificacion.ERROR]
        assert "400" in resultados[0].mensaje
        assert entorno.fuente.solicitudes_realizadas == 2

    @pytest.mark.parametrize("error", [FuenteNoDisponible("sin respuesta"), PresupuestoAgotado("tope diario")])
    def test_fuente_caida_o_presupuesto_agotado_detiene_el_lote(self, entorno, error):
        for radicado in (RADICADO, RADICADO_2, "76001310300120220011111"):
            entorno.servicio.agregar(radicado)
        entorno.fuente.error_busqueda = error
        resultados = entorno.servicio.verificar_todos()
        assert [r.estado for r in resultados] == [EstadoVerificacion.OMITIDO] * 3
        assert str(error) in resultados[0].mensaje
        assert resultados[1].mensaje.startswith("Lote detenido")
        assert entorno.fuente.solicitudes_realizadas == 1, "no se insiste contra una fuente caída"
        assert len(entorno.repo.listar_verificaciones()) == 3

    def test_pausa_entre_radicados(self, entorno):
        for radicado in (RADICADO, RADICADO_2, "76001310300120220011111"):
            entorno.servicio.agregar(radicado)
        entorno.servicio.verificar_todos()
        assert entorno.reloj.esperas == [2.0, 2.0]

    def test_filtro_por_radicado(self, entorno):
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.agregar(RADICADO_2)
        resultados = entorno.servicio.verificar_todos([RADICADO_2])
        assert [r.radicado for r in resultados] == [RADICADO_2]

    def test_sin_radicados(self, entorno):
        assert entorno.servicio.verificar_todos() == []


class TestConsultaPuntual:
    def test_devuelve_autos_detalle_y_no_persiste(self, entorno):
        registrar_base(entorno.fuente)
        consulta = entorno.servicio.consultar(RADICADO)
        assert len(consulta.actuaciones) == 3 and len(consulta.autos) == 1
        assert [a.consecutivo for a in consulta.actuaciones] == [1, 2, 3], "orden cronológico"
        assert len(consulta.detalles) == 1 and consulta.detalles[0].ponente == "PONENTE DE PRUEBA"
        assert entorno.fuente.solicitudes_realizadas == 3
        assert entorno.repo.listar_vigilados(solo_activos=False) == []
        assert entorno.repo.ids_actuaciones_conocidas(RADICADO) == set()

    def test_sin_detalle_ahorra_una_solicitud(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.consultar(RADICADO, incluir_detalle=False)
        assert entorno.fuente.solicitudes_realizadas == 2

    def test_no_encontrado(self, entorno):
        with pytest.raises(ProcesoNoEncontrado):
            entorno.servicio.consultar(RADICADO)

    def test_proceso_privado_solo_devuelve_el_resumen(self, entorno):
        entorno.fuente.registrar(hacer_proceso(es_privado=True))
        consulta = entorno.servicio.consultar(RADICADO)
        assert consulta.procesos[0].es_privado and consulta.actuaciones == [] and consulta.detalles == []


def test_recorrer_actuaciones_respeta_max_paginas(fuente: FuenteFalsa):
    fuente.por_pagina = 1
    fuente.registrar(hacer_proceso(), actuaciones_base(date(2024, 5, 10)))
    leidas = list(recorrer_actuaciones(fuente, ID_PROCESO, max_paginas=2))
    assert [a.consecutivo for a in leidas] == [3, 2]
    assert fuente.solicitudes_realizadas == 2


def test_recorrer_actuaciones_se_detiene_en_lo_conocido(fuente: FuenteFalsa):
    fuente.por_pagina = 1
    fuente.registrar(hacer_proceso(), actuaciones_base(date(2024, 5, 10)))
    leidas = list(recorrer_actuaciones(fuente, ID_PROCESO, max_paginas=10, detener_si=lambda a: a.id_registro == 1002))
    assert [a.id_registro for a in leidas] == [1003]
    assert fuente.solicitudes_realizadas == 2


class TestFichaDelProceso:
    def test_linea_base_guarda_la_ficha_y_la_refresca_al_haber_cambios(self, fuente: FuenteFalsa, notificador: NotificadorRegistro):
        from consultor_procesos.dominio.modelos import DetalleProceso

        repo = RepositorioMemoria()
        reloj = RelojCalendario(AHORA)
        servicio = ServicioVigilancia(
            fuente, repo, notificador, opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0), ahora=reloj, dormir=reloj.dormir
        )
        registrar_base(fuente)
        fuente.detalles[ID_PROCESO] = DetalleProceso(
            id_proceso=ID_PROCESO, radicado=RADICADO, despacho=DESPACHO + " ", ponente="PONENTE INICIAL",
            tipo_proceso="Ejecutivo", clase_proceso="Ejecutivo Singular", codigo_despacho=RADICADO[:12],
        )
        servicio.agregar(RADICADO)
        servicio.verificar_todos()
        vigilado = repo.obtener_vigilado(RADICADO)
        assert vigilado.despacho == DESPACHO and vigilado.sujetos.startswith("Demandante:") and vigilado.departamento == "BOGOTÁ"
        assert vigilado.tipo_proceso == "Ejecutivo" and vigilado.clase_proceso == "Ejecutivo Singular" and vigilado.ponente == "PONENTE INICIAL"
        assert vigilado.fecha_proceso == date(2024, 1, 15) and vigilado.titulo.startswith("Demandante:")
        assert fuente.llamadas.count(("detalle", ID_PROCESO)) == 1

        # Sin cambios en el expediente no se vuelve a pedir la ficha.
        reloj.avanzar(hours=1)
        servicio.verificar_todos()
        assert fuente.llamadas.count(("detalle", ID_PROCESO)) == 1

        # Con una actuación nueva se relee: aquí el proceso pasó a segunda instancia.
        fuente.detalles[ID_PROCESO] = DetalleProceso(
            id_proceso=ID_PROCESO, radicado=RADICADO, despacho="TRIBUNAL SUPERIOR DE PRUEBA", ponente="PONENTE NUEVO",
            tipo_proceso="Ejecutivo", clase_proceso="Ejecutivo Singular", codigo_despacho="110013103001",
        )
        fuente.agregar_actuacion(ID_PROCESO, hacer_actuacion(1004, 4, "Auto", "AUTO CONCEDE APELACION", date(2026, 9, 2)), nueva_fecha_ultima=date(2026, 9, 2))
        reloj.avanzar(hours=1)
        servicio.verificar_todos()
        vigilado = repo.obtener_vigilado(RADICADO)
        assert fuente.llamadas.count(("detalle", ID_PROCESO)) == 2
        assert vigilado.ponente == "PONENTE NUEVO" and vigilado.despacho == "TRIBUNAL SUPERIOR DE PRUEBA"
        assert vigilado.codigos_despacho == (RADICADO[:12], "110013103001"), "el tribunal también se vigila en publicaciones"

    def test_el_alias_manda_sobre_los_sujetos_en_el_titulo(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO, alias="Mi caso")
        entorno.servicio.verificar_todos()
        vigilado = entorno.repo.obtener_vigilado(RADICADO)
        assert vigilado.sujetos.startswith("Demandante:") and vigilado.titulo == "Mi caso"

    def test_radicado_de_una_version_anterior_completa_su_ficha_sin_cambios(self, fuente: FuenteFalsa, notificador: NotificadorRegistro):
        """Un vigilado ya inicializado pero sin ficha (base de datos vieja) la lee una sola vez, aunque no haya novedades."""
        from consultor_procesos.dominio.modelos import ProcesoVigilado

        repo = RepositorioMemoria()
        reloj = RelojCalendario(AHORA)
        servicio = ServicioVigilancia(
            fuente, repo, notificador, opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0), ahora=reloj, dormir=reloj.dormir
        )
        registrar_base(fuente, fecha_ultima=date(2024, 5, 10))
        repo.guardar_vigilado(ProcesoVigilado(
            radicado=RADICADO, id_proceso=ID_PROCESO, huella=f"{ID_PROCESO}:2024-05-10", fecha_ultima_actuacion=date(2024, 5, 10),
            ultima_verificacion=AHORA, ultima_lectura_actuaciones=AHORA, inicializado=True, creado_en=AHORA,
        ))
        for a in actuaciones_base(date(2024, 5, 10)):
            repo.guardar_novedades([servicio._detector.evaluar(a, DESPACHO)], AHORA)

        reloj.avanzar(hours=1)
        [resultado] = servicio.verificar_todos()
        assert resultado.estado is EstadoVerificacion.SIN_CAMBIOS
        vigilado = repo.obtener_vigilado(RADICADO)
        assert vigilado.despacho == DESPACHO and vigilado.sujetos.startswith("Demandante:")
        assert vigilado.ponente == "PONENTE DE PRUEBA" and vigilado.ficha_leida_en == reloj.ahora
        assert fuente.llamadas.count(("detalle", ID_PROCESO)) == 1

        reloj.avanzar(hours=1)
        servicio.verificar_todos()
        assert fuente.llamadas.count(("detalle", ID_PROCESO)) == 1, "la ficha no se vuelve a pedir si nada cambió"


class TestSinHuecosNiSilencios:
    def test_muchas_actuaciones_entre_dos_verificaciones_se_leen_todas(self, entorno):
        """Antes la lectura incremental paraba en 3 páginas: con 200 nuevas se perdían 80 para siempre."""
        entorno.fuente.por_pagina = 40
        registrar_base(entorno.fuente)
        entorno.servicio.opciones.max_paginas_inicial = 20
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(days=1)
        for i in range(200):
            entorno.fuente.agregar_actuacion(
                ID_PROCESO, hacer_actuacion(5000 + i, 4 + i, fecha=date(2026, 9, 1)), nueva_fecha_ultima=date(2026, 9, 1)
            )

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.OK and len(resultado.novedades) == 200
        assert len(entorno.repo.ids_actuaciones_conocidas(RADICADO)) == 203
        assert "Atención" not in resultado.mensaje
        paginas = [l[2] for l in entorno.fuente.llamadas if l[0] == "actuaciones"]
        assert paginas[-6:] == [1, 2, 3, 4, 5, 6], "leyó hasta empalmar con lo conocido (página 6), no más"

    def test_si_ni_el_tope_alcanza_el_resultado_lo_advierte(self, entorno):
        entorno.fuente.por_pagina = 1
        registrar_base(entorno.fuente)
        entorno.servicio.opciones.max_paginas_inicial = 5
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(days=1)
        for i in range(8):
            entorno.fuente.agregar_actuacion(ID_PROCESO, hacer_actuacion(6000 + i, 4 + i, fecha=date(2026, 9, 1)), date(2026, 9, 1))

        [resultado] = entorno.servicio.verificar_todos()

        assert len(resultado.novedades) == 5
        assert "puede faltar historial intermedio" in resultado.mensaje

    def test_cambio_de_estructura_en_la_ficha_deja_la_verificacion_en_error(self, fuente, notificador):
        from consultor_procesos.dominio.errores import RespuestaInesperada

        class FuenteConFichaCambiada(FuenteFalsa):
            def obtener_detalle(self, id_proceso):
                raise RespuestaInesperada("La respuesta de la fuente en el detalle del proceso no trae 'despacho'")

        fuente_cambiada = FuenteConFichaCambiada()
        registrar_base(fuente_cambiada)
        repo = RepositorioMemoria()
        servicio = ServicioVigilancia(fuente_cambiada, repo, notificador, opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0))
        servicio.agregar(RADICADO)

        [resultado] = servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.ERROR and "'despacho'" in resultado.mensaje
        assert repo.listar_verificaciones(RADICADO)[0]["estado"] == "ERROR"

    def test_cambio_de_estructura_al_listar_documentos_deja_la_verificacion_en_error(self, entorno):
        from consultor_procesos.dominio.errores import RespuestaInesperada

        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        entorno.reloj.avanzar(days=1)
        nueva = hacer_actuacion(1004, 4, anotacion="AUTO DECRETA PRUEBAS", fecha=date(2026, 9, 1), con_documentos=True)
        entorno.fuente.agregar_actuacion(ID_PROCESO, nueva, nueva_fecha_ultima=date(2026, 9, 1))
        entorno.fuente.error_documentos = RespuestaInesperada("Un documento de la actuación no trae identificador")

        [resultado] = entorno.servicio.verificar_todos()

        assert resultado.estado == EstadoVerificacion.ERROR
        assert 1004 not in entorno.repo.ids_actuaciones_conocidas(RADICADO), "se reintentará en la próxima verificación"


class TestSimularNovedad:
    def test_la_actuacion_olvidada_vuelve_como_novedad_y_se_notifica(self, entorno):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO, alias="Demo")
        entorno.servicio.verificar_todos()
        assert entorno.notificador.eventos == [], "la línea base no notifica"
        entorno.reloj.avanzar(hours=1)

        olvidadas = entorno.servicio.simular_novedad(RADICADO, cantidad=2)
        [resultado] = entorno.servicio.verificar_todos()

        assert olvidadas == [1003, 1002], "las más recientes primero"
        assert resultado.estado == EstadoVerificacion.OK and not resultado.es_linea_base
        assert sorted(n.actuacion.id_registro for n in resultado.novedades) == [1002, 1003]
        assert [n.actuacion.id_registro for n in resultado.autos] == [1002], "la 1002 es 'AUTO ADMITE DEMANDA'"
        [evento] = entorno.notificador.eventos
        assert evento.alias == "Demo" and len(evento.novedades) == 2

    @pytest.mark.parametrize("cantidad", [0, 3, 10])
    def test_cantidad_fuera_de_rango(self, entorno, cantidad):
        registrar_base(entorno.fuente)
        entorno.servicio.agregar(RADICADO)
        entorno.servicio.verificar_todos()
        with pytest.raises(ValueError, match="entre 1 y 2"):
            entorno.servicio.simular_novedad(RADICADO, cantidad=cantidad)

    def test_radicado_sin_linea_base(self, entorno):
        entorno.servicio.agregar(RADICADO)
        with pytest.raises(ProcesoNoEncontrado):
            entorno.servicio.simular_novedad(RADICADO)
