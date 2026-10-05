"""Pruebas de lo que hace falta para exponer la interfaz en un servidor: usuarios, CSRF, cabeceras y monitoreo."""

from __future__ import annotations

import base64
import threading
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from apoyo import RADICADO, FuenteFalsa, NotificadorRegistro, hacer_actuacion, hacer_proceso
from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
from consultor_procesos.adaptadores.web.seguridad import (
    Autenticador,
    generar_hash,
    leer_usuarios,
    verificar_hash,
)
from consultor_procesos.adaptadores.web.servidor import AplicacionWeb, ServidorWeb
from consultor_procesos.aplicacion.horario import Horario
from consultor_procesos.aplicacion.servicio_vigilancia import OpcionesVerificacion, ServicioVigilancia
from consultor_procesos.dominio.modelos import EstadoVerificacion, ResultadoVerificacion

AHORA = datetime(2026, 9, 2, 10, 0)  # miércoles
CLAVE = "clave-de-prueba-1"


def basic(usuario: str, clave: str) -> str:
    return "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()


@pytest.fixture(scope="module")
def hash_ana() -> str:
    return generar_hash(CLAVE)


class Reloj:
    def __init__(self, inicio: datetime) -> None:
        self.ahora = inicio

    def __call__(self) -> datetime:
        return self.ahora


class PlanificadorQuieto:
    """Planificador que no ejecuta ciclos: solo espera a que lo detengan."""

    def __init__(self) -> None:
        self._parar = threading.Event()
        self.ciclos_ejecutados = 0
        self.proxima_ejecucion = None

    def ejecutar(self) -> None:
        self._parar.wait(10)

    def detener(self) -> None:
        self._parar.set()


def hacer_aplicacion(
    fuente: FuenteFalsa,
    notificador: NotificadorRegistro,
    tmp_path: Path,
    autenticador: Autenticador | None = None,
    reloj: Reloj | None = None,
    horario: Horario | None = None,
) -> AplicacionWeb:
    reloj = reloj or Reloj(AHORA)
    repo = RepositorioMemoria()
    servicio = ServicioVigilancia(
        fuente,
        repo,
        notificador,
        opciones=OpcionesVerificacion(pausa_entre_procesos_segundos=0, descubrir_despachos=False),
        ahora=reloj,
        dormir=lambda s: None,
        directorio_documentos=tmp_path / "docs",
    )
    return AplicacionWeb(
        servicio,
        repo,
        fabrica_planificador=lambda ciclo: PlanificadorQuieto(),
        ahora=reloj,
        horario=horario,
        autenticador=autenticador,
        commit="abc1234",
        avisos_configuracion=["Falta el correo de contacto (CONSULTOR_CONTACTO)."],
    )


# --- contraseñas y usuarios --------------------------------------------------------------------


def test_hash_de_contrasena(hash_ana):
    assert hash_ana.startswith("scrypt:16384:8:1:") and "$" not in hash_ana and "," not in hash_ana
    assert verificar_hash(CLAVE, hash_ana) and not verificar_hash("otra", hash_ana)
    assert generar_hash(CLAVE) != hash_ana, "cada hash lleva su propia sal"
    assert not verificar_hash(CLAVE, "basura") and not verificar_hash(CLAVE, "md5:1:2:3:4:5")


def test_leer_usuarios(hash_ana):
    assert leer_usuarios(f"ana:{hash_ana}, luis:{hash_ana}\n") == {"ana": hash_ana, "luis": hash_ana}
    assert leer_usuarios("") == {} and leer_usuarios(None) == {}
    with pytest.raises(ValueError, match="crear-usuario"):
        leer_usuarios("ana:clave-en-claro")


# --- autorización (sin abrir puertos) --------------------------------------------------------


@pytest.fixture
def protegida(fuente, notificador, tmp_path, hash_ana) -> AplicacionWeb:
    return hacer_aplicacion(fuente, notificador, tmp_path, autenticador=Autenticador({"ana": hash_ana}))


def test_sin_credenciales_pide_usuario_y_salud_es_publica(protegida: AplicacionWeb):
    estado, contenido, _, cabeceras = protegida.autorizar("GET", "/api/estado", {}, "10.0.0.1")
    assert estado == 401 and cabeceras["WWW-Authenticate"].startswith("Basic realm=")
    assert protegida.autorizar("GET", "/", {}, "10.0.0.1")[0] == 401, "la página también exige usuario"
    assert protegida.autorizar("GET", "/api/salud", {}, "10.0.0.1") is None
    assert protegida.autorizar("GET", "/api/estado", {"Authorization": basic("ana", CLAVE)}, "10.0.0.1") is None
    assert protegida.autorizar("GET", "/api/estado", {"Authorization": basic("ana", "mala")}, "10.0.0.1")[0] == 401
    assert protegida.autorizar("GET", "/api/estado", {"Authorization": basic("nadie", CLAVE)}, "10.0.0.1")[0] == 401


def test_csrf_y_origen(protegida: AplicacionWeb):
    credenciales = {"Authorization": basic("ana", CLAVE)}
    assert protegida.autorizar("POST", "/api/verificar", credenciales, "10.0.0.1")[0] == 403
    assert protegida.autorizar("DELETE", f"/api/vigilados/{RADICADO}", credenciales, "10.0.0.1")[0] == 403
    con_csrf = {**credenciales, "X-Requested-With": "XMLHttpRequest"}
    assert protegida.autorizar("POST", "/api/verificar", con_csrf, "10.0.0.1") is None
    otro_sitio = {**credenciales, "Sec-Fetch-Site": "cross-site"}
    assert protegida.autorizar("GET", "/api/consultar/" + RADICADO, otro_sitio, "10.0.0.1")[0] == 403
    assert protegida.autorizar("GET", "/", otro_sitio, "10.0.0.1") is None, "abrir la página desde un enlace sí se permite"


def test_bloqueo_por_ip_tras_intentos_fallidos(hash_ana):
    reloj = [0.0]
    autenticador = Autenticador({"ana": hash_ana}, max_fallos=3, segundos_bloqueo=60, reloj=lambda: reloj[0])
    for _ in range(3):
        assert not autenticador.autenticar(basic("ana", "mala"), "1.1.1.1").aceptado
    bloqueo = autenticador.autenticar(basic("ana", CLAVE), "1.1.1.1")
    assert bloqueo.bloqueado and 0 < bloqueo.segundos_bloqueo <= 61, "bloqueada aunque ahora acierte"
    assert autenticador.autenticar(basic("ana", CLAVE), "2.2.2.2").usuario == "ana", "otra IP no se afecta"
    reloj[0] = 61
    assert autenticador.autenticar(basic("ana", CLAVE), "1.1.1.1").usuario == "ana"


def test_sin_usuarios_no_se_exige_autenticacion(fuente, notificador, tmp_path):
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path, autenticador=Autenticador({}))
    assert aplicacion.autorizar("GET", "/api/estado", {}, "127.0.0.1") is None
    assert aplicacion.autorizar("POST", "/api/verificar", {}, "127.0.0.1")[0] == 403, "el CSRF aplica siempre"


# --- salud y monitor -----------------------------------------------------------------------------


def test_salud(fuente, notificador, tmp_path):
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path)
    estado, contenido, *_ = aplicacion.manejar("GET", "/api/salud", {}, None)
    assert estado == 200 and contenido == {"estado": "ok", "version": contenido["version"], "commit": "abc1234"}


def test_monitor_avisa_si_la_vigilancia_esta_detenida(fuente, notificador, tmp_path):
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path)
    estado, monitor, *_ = aplicacion.manejar("GET", "/api/monitor", {}, None)
    assert estado == 200 and monitor["ok"] is False
    assert any("detenida" in p for p in monitor["problemas"])
    assert "Falta el correo de contacto (CONSULTOR_CONTACTO)." in monitor["avisos"]
    assert "No hay procesos en vigilancia." in monitor["avisos"]

    aplicacion.vigilante.iniciar()
    try:
        monitor = aplicacion.manejar("GET", "/api/monitor", {}, None)[1]
        assert monitor["ok"] is True and monitor["vigilancia"]["activa"] is True and monitor["commit"] == "abc1234"
    finally:
        aplicacion.vigilante.detener()


def test_monitor_detecta_una_verificacion_programada_que_no_corrio(fuente, notificador, tmp_path):
    reloj = Reloj(datetime(2026, 9, 2, 6, 30))
    horario = Horario.desde_texto(["07:00", "17:00"], hasta="18:00")
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path, reloj=reloj, horario=horario)
    aplicacion._servicio.agregar(RADICADO)
    aplicacion.vigilante.iniciar()
    try:
        reloj.ahora = datetime(2026, 9, 2, 7, 30)
        assert aplicacion.manejar("GET", "/api/monitor", {}, None)[1]["ok"] is True, "aún dentro de la tolerancia"
        reloj.ahora = datetime(2026, 9, 2, 8, 0)
        monitor = aplicacion.manejar("GET", "/api/monitor", {}, None)[1]
        assert monitor["ok"] is False and any("07:00" in p for p in monitor["problemas"])

        aplicacion._repositorio.registrar_verificacion(
            ResultadoVerificacion(RADICADO, EstadoVerificacion.OK, datetime(2026, 9, 2, 7, 50))
        )
        assert aplicacion.manejar("GET", "/api/monitor", {}, None)[1]["ok"] is True
    finally:
        aplicacion.vigilante.detener()


def test_monitor_reporta_procesos_en_error(fuente, notificador, tmp_path):
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path)
    aplicacion._servicio.agregar(RADICADO)
    aplicacion._repositorio.registrar_verificacion(
        ResultadoVerificacion(
            RADICADO, EstadoVerificacion.ERROR, AHORA - timedelta(hours=1), mensaje="no trae 'idRegActuacion'"
        )
    )
    aplicacion.vigilante.iniciar()
    try:
        monitor = aplicacion.manejar("GET", "/api/monitor", {}, None)[1]
    finally:
        aplicacion.vigilante.detener()
    assert monitor["ok"] is False and "idRegActuacion" in monitor["problemas"][0]


# --- transporte HTTP real ---------------------------------------------------------------------------


def test_servidor_real_con_usuarios_cabeceras_y_proxy(fuente, notificador, tmp_path, hash_ana):
    fuente.registrar(hacer_proceso(), [hacer_actuacion(1, 1)])
    autenticador = Autenticador({"ana": hash_ana}, max_fallos=2)
    aplicacion = hacer_aplicacion(fuente, notificador, tmp_path, autenticador=autenticador)
    servidor = ServidorWeb(aplicacion, host="127.0.0.1", puerto=0, confiar_proxy=True)
    host, puerto = servidor.iniciar()
    base = f"http://{host}:{puerto}"
    try:
        with httpx.Client(base_url=base, timeout=5) as anonimo:
            assert anonimo.get("/api/salud").json()["estado"] == "ok"
            sin_clave = anonimo.get("/")
            assert sin_clave.status_code == 401 and "WWW-Authenticate" in sin_clave.headers
            assert "Python" not in sin_clave.headers.get("Server", "")

        with httpx.Client(base_url=base, timeout=5, auth=("ana", CLAVE)) as ana:
            pagina = ana.get("/")
            assert pagina.status_code == 200
            assert pagina.headers["X-Content-Type-Options"] == "nosniff"
            assert "frame-ancestors 'self'" in pagina.headers["Content-Security-Policy"]
            estado = ana.get("/api/estado")
            assert estado.status_code == 200 and estado.headers["Cache-Control"] == "no-store"
            assert ana.post("/api/vigilados", json={"radicado": RADICADO}).status_code == 403, "falta la cabecera CSRF"
            csrf = {"X-Requested-With": "XMLHttpRequest"}
            assert ana.post("/api/vigilados", json={"radicado": RADICADO}, headers=csrf).status_code == 201
            enorme = ana.post("/api/vigilados", content=b"x" * 70_000, headers={**csrf, "Content-Type": "application/json"})
            assert enorme.status_code == 413

        # Detrás de Caddy cuenta la IP de X-Forwarded-For: los fallos de una IP no bloquean a otra.
        with httpx.Client(base_url=base, timeout=5) as http:
            atacante = {"X-Forwarded-For": "203.0.113.9"}
            for _ in range(2):
                assert http.get("/api/estado", headers=atacante, auth=("ana", "mala")).status_code == 401
            assert http.get("/api/estado", headers=atacante, auth=("ana", CLAVE)).status_code == 429
            legitimo = {"X-Forwarded-For": "198.51.100.7"}
            assert http.get("/api/estado", headers=legitimo, auth=("ana", CLAVE)).status_code == 200
    finally:
        servidor.detener()
