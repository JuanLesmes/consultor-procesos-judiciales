"""Las dos implementaciones del puerto Repositorio deben comportarse igual."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from apoyo import RADICADO, RADICADO_2, hacer_actuacion
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.adaptadores.persistencia.sqlite import RepositorioSQLite
from consultor_procesos.dominio.errores import PresupuestoAgotado
from consultor_procesos.dominio.modelos import EstadoVerificacion, Novedad, ProcesoVigilado, ResultadoVerificacion

MOMENTO = datetime(2026, 9, 2, 10, 30)


@pytest.fixture(params=["memoria", "sqlite"])
def repositorio(request, tmp_path):
    if request.param == "memoria":
        repo = RepositorioMemoria()
    else:
        repo = RepositorioSQLite(tmp_path / "prueba.sqlite")
    yield repo
    repo.cerrar()


def novedad(id_registro: int, consecutivo: int, es_auto: bool, radicado: str = RADICADO) -> Novedad:
    actuacion = hacer_actuacion(id_registro, consecutivo, anotacion="AUTO" if es_auto else "OTRA", radicado=radicado)
    return Novedad(radicado=radicado, actuacion=actuacion, es_auto=es_auto, coincidencias=("AUTO",) if es_auto else (), despacho="JUZGADO")


class TestVigilados:
    def test_guardar_y_obtener_conserva_todos_los_campos(self, repositorio):
        vigilado = ProcesoVigilado(
            radicado=RADICADO,
            alias="Caso demo",
            id_proceso=99,
            huella="99:2024-05-10",
            fecha_ultima_actuacion=date(2024, 5, 10),
            ultima_verificacion=MOMENTO,
            ultima_lectura_actuaciones=MOMENTO,
            inicializado=True,
            activo=True,
            creado_en=MOMENTO,
            despachos=("050013103003",),
            despacho="JUZGADO 001 CIVIL MUNICIPAL DE PRUEBA",
            departamento="BOGOTÁ",
            sujetos="Demandante: PERSONA DE PRUEBA | Demandado: ENTIDAD DE PRUEBA",
            tipo_proceso="Ejecutivo",
            clase_proceso="Ejecutivo Singular",
            ponente="PONENTE DE PRUEBA",
            fecha_proceso=date(2024, 1, 15),
            ficha_leida_en=MOMENTO,
        )
        repositorio.guardar_vigilado(vigilado)
        leido = repositorio.obtener_vigilado(RADICADO)
        assert leido == vigilado
        assert leido.titulo == "Caso demo" and leido.codigos_despacho == (RADICADO[:12], "050013103003")

    def test_inexistente(self, repositorio):
        assert repositorio.obtener_vigilado(RADICADO) is None

    def test_actualizar(self, repositorio):
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO, creado_en=MOMENTO))
        vigilado = repositorio.obtener_vigilado(RADICADO)
        vigilado.alias = "nuevo alias"
        vigilado.inicializado = True
        repositorio.guardar_vigilado(vigilado)
        leido = repositorio.obtener_vigilado(RADICADO)
        assert leido.alias == "nuevo alias" and leido.inicializado and leido.creado_en == MOMENTO
        assert len(repositorio.listar_vigilados(solo_activos=False)) == 1

    def test_listar_solo_activos_ordenado(self, repositorio):
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO, activo=True))
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO_2, activo=False))
        assert [v.radicado for v in repositorio.listar_vigilados()] == [RADICADO]
        assert [v.radicado for v in repositorio.listar_vigilados(solo_activos=False)] == sorted([RADICADO, RADICADO_2])

    def test_eliminar_borra_tambien_las_actuaciones(self, repositorio):
        repositorio.guardar_vigilado(ProcesoVigilado(radicado=RADICADO))
        repositorio.guardar_novedades([novedad(1, 1, True)], MOMENTO)
        assert repositorio.eliminar_vigilado(RADICADO)
        assert not repositorio.eliminar_vigilado(RADICADO)
        assert repositorio.obtener_vigilado(RADICADO) is None
        assert repositorio.ids_actuaciones_conocidas(RADICADO) == set()


class TestActuaciones:
    def test_guardar_es_idempotente(self, repositorio):
        novedades = [novedad(1, 1, False), novedad(2, 2, True)]
        assert repositorio.guardar_novedades(novedades, MOMENTO) == 2
        assert repositorio.guardar_novedades(novedades, MOMENTO) == 0
        assert repositorio.ids_actuaciones_conocidas(RADICADO) == {1, 2}

    def test_listar_solo_autos_y_orden_cronologico(self, repositorio):
        repositorio.guardar_novedades([novedad(30, 3, True), novedad(10, 1, False), novedad(20, 2, True)], MOMENTO)
        todas = repositorio.listar_actuaciones(RADICADO)
        assert [n.actuacion.consecutivo for n in todas] == [1, 2, 3]
        autos = repositorio.listar_actuaciones(RADICADO, solo_autos=True)
        assert [n.actuacion.id_registro for n in autos] == [20, 30]
        assert autos[0].coincidencias == ("AUTO",) and autos[0].despacho == "JUZGADO"
        assert autos[0].actuacion.fecha_actuacion == date(2024, 5, 10)

    def test_las_actuaciones_se_separan_por_radicado(self, repositorio):
        repositorio.guardar_novedades([novedad(1, 1, True), novedad(2, 1, True, radicado=RADICADO_2)], MOMENTO)
        assert repositorio.ids_actuaciones_conocidas(RADICADO) == {1}
        assert repositorio.ids_actuaciones_conocidas(RADICADO_2) == {2}


class TestVerificaciones:
    def test_registrar_y_listar_mas_recientes_primero(self, repositorio):
        primero = ResultadoVerificacion(RADICADO, EstadoVerificacion.OK, MOMENTO, novedades=[novedad(1, 1, True)], mensaje="uno", solicitudes=2)
        segundo = ResultadoVerificacion(RADICADO_2, EstadoVerificacion.ERROR, MOMENTO, mensaje="dos")
        repositorio.registrar_verificacion(primero)
        repositorio.registrar_verificacion(segundo)
        filas = repositorio.listar_verificaciones()
        assert [f["radicado"] for f in filas] == [RADICADO_2, RADICADO]
        assert filas[1] == {
            "radicado": RADICADO,
            "momento": MOMENTO,
            "estado": "OK",
            "novedades": 1,
            "autos": 1,
            "solicitudes": 2,
            "mensaje": "uno",
        }
        assert [f["estado"] for f in repositorio.listar_verificaciones(RADICADO_2)] == ["ERROR"]
        assert len(repositorio.listar_verificaciones(limite=1)) == 1


class TestContadores:
    def test_incrementar_por_fecha(self, repositorio):
        hoy = date(2026, 9, 2)
        assert repositorio.obtener_contador(hoy) == 0
        assert repositorio.incrementar_contador(hoy) == 1
        assert repositorio.incrementar_contador(hoy, 2) == 3
        assert repositorio.obtener_contador(date(2026, 9, 3)) == 0


def test_sqlite_conserva_los_datos_al_reabrir(tmp_path):
    ruta = tmp_path / "persistente.sqlite"
    with RepositorioSQLite(ruta) as repo:
        repo.guardar_vigilado(ProcesoVigilado(radicado=RADICADO, alias="persistente"))
        repo.guardar_novedades([novedad(1, 1, True)], MOMENTO)
        repo.incrementar_contador(date(2026, 9, 2), 5)
    with RepositorioSQLite(ruta) as repo:
        assert repo.obtener_vigilado(RADICADO).alias == "persistente"
        assert repo.ids_actuaciones_conocidas(RADICADO) == {1}
        assert repo.obtener_contador(date(2026, 9, 2)) == 5


def test_contar_pendientes_por_radicado(repositorio):
    novedades = [novedad(1, 1, True), novedad(2, 2, False), novedad(3, 1, True, radicado=RADICADO_2)]
    repositorio.guardar_novedades(novedades, datetime(2026, 9, 2, 10, 0))
    assert repositorio.contar_pendientes_por_radicado() == {
        RADICADO: {"total": 2, "autos": 1},
        RADICADO_2: {"total": 1, "autos": 1},
    }
    repositorio.marcar_revisada(3)
    assert repositorio.contar_pendientes_por_radicado() == {RADICADO: {"total": 2, "autos": 1}}


def test_incrementar_si_menor_respeta_el_tope(repositorio):
    hoy = date(2026, 9, 2)
    assert [repositorio.incrementar_si_menor(hoy, 3) for _ in range(5)] == [1, 2, 3, None, None]
    assert repositorio.obtener_contador(hoy) == 3
    assert repositorio.incrementar_si_menor(date(2026, 9, 3), 3) == 1, "cada día empieza de cero"


def test_presupuesto_compartido_entre_dos_conexiones_sqlite(tmp_path):
    """Dos procesos sobre la misma base (por ejemplo, la interfaz y un 'verificar' manual) comparten el tope."""
    from consultor_procesos.adaptadores.persistencia.sqlite import RepositorioSQLite
    from consultor_procesos.infraestructura.cortesia import PresupuestoDiario

    ruta = tmp_path / "compartida.sqlite"
    hoy = date(2026, 9, 2)
    with RepositorioSQLite(ruta) as uno, RepositorioSQLite(ruta) as otro:
        a = PresupuestoDiario(3, uno, hoy=lambda: hoy)
        b = PresupuestoDiario(3, otro, hoy=lambda: hoy)
        assert [a.consumir(), b.consumir(), a.consumir()] == [1, 2, 3]
        with pytest.raises(PresupuestoAgotado):
            b.consumir()


def test_respaldo_sqlite_es_una_copia_consistente(tmp_path):
    from consultor_procesos.adaptadores.persistencia.sqlite import RepositorioSQLite

    with RepositorioSQLite(tmp_path / "base.sqlite") as repo:
        repo.guardar_vigilado(ProcesoVigilado(radicado=RADICADO, alias="Demo"))
        destino = repo.respaldar(tmp_path / "respaldos" / "copia.sqlite")
    with RepositorioSQLite(destino) as copia:
        assert copia.obtener_vigilado(RADICADO).alias == "Demo"
