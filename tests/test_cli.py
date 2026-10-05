"""Pruebas de la CLI de extremo a extremo con fuente falsa y base de datos temporal."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pytest

from apoyo import ID_PROCESO, RADICADO, FuenteFalsa, hacer_actuacion, hacer_proceso
from consultor_procesos import cli


@pytest.fixture
def config_prueba(tmp_path: Path) -> Path:
    ruta = tmp_path / "config.toml"
    ruta.write_text(
        """
[general]
nivel_registro = "WARNING"

[cortesia]
pausa_entre_procesos_segundos = 0

[notificaciones]
archivo_jsonl = ""
""",
        encoding="utf-8",
    )
    return ruta


class Ejecutor:
    def __init__(self, tmp_path: Path, config: Path, fuente: FuenteFalsa, fuente_publicaciones=None) -> None:
        self.bd = tmp_path / "bd.sqlite"
        self.config = config
        self.fuente = fuente
        self.fuente_publicaciones = fuente_publicaciones

    def __call__(self, *args: str) -> tuple[int, str]:
        salida = io.StringIO()
        argv = ["--bd", str(self.bd), "--config", str(self.config), *args]
        codigo = cli.main(
            argv,
            fabrica_fuente=lambda config, repo: self.fuente,
            fabrica_publicaciones=lambda config, repo: self.fuente_publicaciones,
            salida=salida,
        )
        return codigo, salida.getvalue()


@pytest.fixture
def ejecutar(tmp_path: Path, config_prueba: Path, fuente: FuenteFalsa) -> Ejecutor:
    return Ejecutor(tmp_path, config_prueba, fuente)


def registrar_proceso(fuente: FuenteFalsa) -> None:
    fuente.registrar(
        hacer_proceso(fecha_ultima=date(2024, 5, 10)),
        [
            hacer_actuacion(1, 1, "Radicación de proceso", "", date(2024, 4, 1)),
            hacer_actuacion(2, 2, "Constancia secretarial", "AUTO ADMITE DEMANDA", date(2024, 5, 10)),
        ],
    )


def test_agregar_listar_y_quitar(ejecutar: Ejecutor):
    codigo, texto = ejecutar("agregar", RADICADO, "--alias", "Demo")
    assert codigo == 0 and RADICADO in texto and "Demo" in texto

    codigo, texto = ejecutar("listar")
    assert codigo == 0 and RADICADO in texto and "pendiente" in texto

    codigo, texto = ejecutar("quitar", RADICADO)
    assert codigo == 0 and "retirado" in texto

    codigo, texto = ejecutar("quitar", RADICADO)
    assert codigo == cli.CODIGO_NO_ENCONTRADO


def test_agregar_radicado_invalido(ejecutar: Ejecutor, capsys):
    codigo, _ = ejecutar("agregar", "123")
    assert codigo == cli.CODIGO_USO
    assert "23 dígitos" in capsys.readouterr().err


def test_verificar_sin_radicados(ejecutar: Ejecutor):
    codigo, texto = ejecutar("verificar")
    assert codigo == 0 and "No hay radicados" in texto


def test_flujo_completo_linea_base_y_auto_nuevo(ejecutar: Ejecutor, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    ejecutar("agregar", RADICADO, "--alias", "Demo")

    codigo, texto = ejecutar("verificar")
    assert codigo == 0
    assert "OK" in texto and "Línea base" in texto
    assert "[AUTO]" not in texto, "la línea base no se notifica"

    fuente.agregar_actuacion(
        ID_PROCESO,
        hacer_actuacion(3, 3, "Constancia secretarial", "AUTO FIJA FECHA AUDIENCIA", date(2026, 9, 1)),
        nueva_fecha_ultima=date(2026, 9, 1),
    )
    codigo, texto = ejecutar("verificar")
    assert codigo == 0
    assert "[AUTO] 2026-09-01" in texto and "AUTO FIJA FECHA AUDIENCIA" in texto
    assert "1 auto(s) nuevo(s)" in texto

    codigo, texto = ejecutar("autos", RADICADO)
    assert codigo == 0 and "2 registro(s)" in texto and "AUTO ADMITE DEMANDA" in texto

    codigo, texto = ejecutar("autos", RADICADO, "--todas")
    assert "3 registro(s)" in texto

    codigo, texto = ejecutar("historial", "--radicado", RADICADO)
    assert codigo == 0 and texto.count("OK") >= 2


def test_verificar_notificar_existentes(ejecutar: Ejecutor, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    ejecutar("agregar", RADICADO)
    codigo, texto = ejecutar("verificar", "--notificar-existentes")
    assert codigo == 0 and "[AUTO]" in texto and "linea base" in texto


def test_verificar_devuelve_codigo_de_fuente_cuando_se_omite(ejecutar: Ejecutor, fuente: FuenteFalsa):
    from consultor_procesos.dominio.errores import FuenteNoDisponible

    ejecutar("agregar", RADICADO)
    fuente.error_busqueda = FuenteNoDisponible("caída simulada")
    codigo, texto = ejecutar("verificar")
    assert codigo == cli.CODIGO_FUENTE and "OMITIDO" in texto


def test_consultar_texto_y_json(ejecutar: Ejecutor, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    codigo, texto = ejecutar("consultar", RADICADO)
    assert codigo == 0
    assert "PONENTE DE PRUEBA" in texto and "[AUTO]" in texto and "2 en total, 1 auto(s)" in texto

    codigo, texto = ejecutar("consultar", RADICADO, "--json", "--sin-detalle")
    assert codigo == 0
    datos = json.loads(texto)
    assert datos["radicado"] == RADICADO
    assert datos["resumen"] == {"total_actuaciones": 2, "total_autos": 1}
    assert datos["detalles"] == []
    assert datos["actuaciones"][1]["es_auto"] is True

    codigo, texto = ejecutar("consultar", RADICADO, "--solo-autos")
    assert "[    ]" not in texto and "[AUTO]" in texto


def test_consultar_no_encontrado(ejecutar: Ejecutor, capsys):
    codigo, _ = ejecutar("consultar", RADICADO)
    assert codigo == cli.CODIGO_NO_ENCONTRADO
    assert "No se encontró" in capsys.readouterr().err


def test_consultar_con_fuente_caida(ejecutar: Ejecutor, fuente: FuenteFalsa):
    from consultor_procesos.dominio.errores import FuenteNoDisponible

    fuente.error_busqueda = FuenteNoDisponible("mantenimiento")
    codigo, _ = ejecutar("consultar", RADICADO)
    assert codigo == cli.CODIGO_FUENTE


def test_vigilar_un_ciclo(ejecutar: Ejecutor, fuente: FuenteFalsa):
    registrar_proceso(fuente)
    ejecutar("agregar", RADICADO)
    codigo, texto = ejecutar("vigilar", "--ciclos", "1", "--intervalo-minutos", "1")
    assert codigo == 0
    assert "Vigilando 1 radicado(s)" in texto and "Ciclo de verificación" in texto and "OK" in texto


def test_iniciar_config(tmp_path: Path):
    ruta = tmp_path / "nueva.toml"
    salida = io.StringIO()
    assert cli.main(["iniciar-config", "--ruta", str(ruta)], salida=salida) == 0
    assert ruta.exists() and "agente_usuario" in ruta.read_text(encoding="utf-8")
    assert cli.main(["iniciar-config", "--ruta", str(ruta)], salida=io.StringIO()) == cli.CODIGO_USO


def test_config_inexistente(tmp_path: Path):
    codigo = cli.main(["--config", str(tmp_path / "nada.toml"), "listar"], salida=io.StringIO())
    assert codigo == cli.CODIGO_USO


def test_construir_fuente_real_usa_la_configuracion(tmp_path: Path):
    from consultor_procesos.adaptadores.persistencia.memoria import RepositorioMemoria
    from consultor_procesos.configuracion import Configuracion

    config = Configuracion()
    config.general.agente_usuario = "Prueba/1.0 (contacto: yo@ejemplo.com)"
    fuente = cli.construir_fuente(config, RepositorioMemoria())
    try:
        assert fuente.nombre == "CPNU"
        assert fuente._http.headers["User-Agent"] == "Prueba/1.0 (contacto: yo@ejemplo.com)"
        assert str(fuente._http.base_url).startswith("https://consultaprocesos.ramajudicial.gov.co:448")
    finally:
        fuente.cerrar()


def test_consultar_salida_a_archivo_utf8(ejecutar: Ejecutor, fuente: FuenteFalsa, tmp_path: Path):
    registrar_proceso(fuente)
    destino = tmp_path / "consulta.json"
    codigo, texto = ejecutar("consultar", RADICADO, "--json", "--salida", str(destino))
    assert codigo == 0 and "Resultado escrito" in texto
    contenido = destino.read_text(encoding="utf-8")
    assert json.loads(contenido)["resumen"]["total_autos"] == 1
    assert "Radicación de proceso" in contenido, "las tildes se escriben en UTF-8"


def test_salida_redirigida_se_escribe_en_utf8(tmp_path: Path):
    """Regresión: en Windows la salida redirigida usaba cp1252 y el JSON no se podía leer como UTF-8."""
    import os
    import subprocess
    import sys

    entorno = dict(os.environ)
    entorno["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    entorno.pop("PYTHONIOENCODING", None)
    entorno.pop("PYTHONUTF8", None)
    proceso = subprocess.run(
        [sys.executable, "-m", "consultor_procesos", "--bd", str(tmp_path / "bd.sqlite"), "listar"],
        cwd=tmp_path,
        env=entorno,
        capture_output=True,
        timeout=120,
    )
    assert proceso.returncode == 0, proceso.stderr.decode("utf-8", "replace")
    u_con_tilde = chr(0xDA)  # la U de "Ultima actuacion"
    assert u_con_tilde.encode("utf-8") in proceso.stdout, "la salida debe ir en UTF-8"
    assert u_con_tilde.encode("cp1252") not in proceso.stdout, "no debe haber bytes cp1252"
    proceso.stdout.decode("utf-8")


def test_publicaciones_comando(tmp_path: Path, config_prueba: Path, fuente: FuenteFalsa):
    from apoyo import DESPACHO_CODIGO, FuentePublicacionesFalsa, hacer_publicacion

    fuente_pub = FuentePublicacionesFalsa()
    fuente_pub.registrar(hacer_publicacion("1", titulo=f"Traslado {RADICADO}", documentos=(), fecha=date.today()))
    ejecutar = Ejecutor(tmp_path, config_prueba, fuente, fuente_pub)
    registrar_proceso(fuente)
    ejecutar("agregar", RADICADO, "--alias", "Demo")

    codigo, texto = ejecutar("verificar")
    assert codigo == 0 and "Publicaciones de los despachos" in texto and DESPACHO_CODIGO in texto
    assert "[PUBLICACION]" in texto, "la coincidencia se notifica por consola"

    codigo, texto = ejecutar("publicaciones")
    assert codigo == 0 and "1 publicación(es) mencionan" in texto and "Traslado" in texto

    codigo, texto = ejecutar("publicaciones", "--despacho", DESPACHO_CODIGO)
    assert codigo == 0 and "1 publicación(es) registradas" in texto

    codigo, texto = ejecutar("publicaciones", "--revisar", "--radicado", RADICADO)
    assert codigo == 0 and "OK" in texto


def test_publicaciones_desactivadas(ejecutar: Ejecutor):
    codigo, texto = ejecutar("publicaciones")
    assert codigo == cli.CODIGO_USO and "desactivada" in texto
