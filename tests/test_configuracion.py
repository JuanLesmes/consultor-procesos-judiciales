from pathlib import Path

import pytest

from consultor_procesos.configuracion import (
    ConfigCorreo,
    Configuracion,
    cargar_configuracion,
    contrasena_correo,
    escribir_ejemplo,
)


def test_valores_predeterminados_son_conservadores():
    config = Configuracion()
    assert config.cortesia.solicitudes_por_minuto == 12
    assert config.cortesia.rafaga == 1
    assert config.cortesia.presupuesto_diario_solicitudes == 400
    assert config.verificacion.palabras_clave == ["AUTO"]
    assert config.vigilancia.intervalo_minutos == 240
    assert config.vigilancia.horas == ["07:00", "10:00", "13:00", "17:00"], "horario laboral, 4 veces al día"
    assert config.vigilancia.dias == ["lun", "mar", "mie", "jue", "vie"] and config.vigilancia.hasta == "18:00"
    assert config.vigilancia.iniciar_con_interfaz is True
    assert config.notificaciones.correo.habilitado is False


def test_carga_toml_parcial_e_ignora_claves_desconocidas(tmp_path: Path):
    ruta = tmp_path / "c.toml"
    ruta.write_text(
        """
[general]
agente_usuario = "Prueba/1.0 (contacto: yo@ejemplo.com)"
clave_desconocida = 1

[cortesia]
solicitudes_por_minuto = 6

[verificacion]
palabras_clave = ["AUTO", "SENTENCIA"]

[notificaciones.correo]
habilitado = true
destinatarios = ["a@ejemplo.com", "b@ejemplo.com"]
""",
        encoding="utf-8",
    )
    config = cargar_configuracion(ruta)
    assert config.general.agente_usuario.startswith("Prueba/1.0")
    assert config.cortesia.solicitudes_por_minuto == 6
    assert config.cortesia.rafaga == 1, "lo no indicado conserva el predeterminado"
    assert config.verificacion.palabras_clave == ["AUTO", "SENTENCIA"]
    assert config.notificaciones.correo.habilitado is True
    assert config.notificaciones.correo.destinatarios == ["a@ejemplo.com", "b@ejemplo.com"]
    assert config.notificaciones.consola is True


def test_el_ejemplo_generado_es_valido(tmp_path: Path):
    ruta = escribir_ejemplo(tmp_path / "ejemplo.toml")
    config = cargar_configuracion(ruta)
    assert isinstance(config, Configuracion)
    assert "contacto" in config.general.agente_usuario
    assert config.cortesia.solicitudes_por_minuto == 12


def test_escribir_ejemplo_no_sobrescribe_sin_permiso(tmp_path: Path):
    ruta = escribir_ejemplo(tmp_path / "ejemplo.toml")
    with pytest.raises(FileExistsError):
        escribir_ejemplo(ruta)
    escribir_ejemplo(ruta, sobrescribir=True)


def test_sin_archivo_usa_predeterminados(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cargar_configuracion(None) == Configuracion()


def test_ruta_inexistente(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        cargar_configuracion(tmp_path / "no-existe.toml")


def test_contrasena_de_correo_viene_del_entorno(monkeypatch):
    monkeypatch.setenv("MI_CLAVE", "secreto")
    assert contrasena_correo(ConfigCorreo(contrasena_env="MI_CLAVE")) == "secreto"
    assert contrasena_correo(ConfigCorreo(contrasena_env="NO_EXISTE_XYZ")) == ""
    assert contrasena_correo(ConfigCorreo(contrasena_env="")) == ""


def test_horario_de_vigilancia_desde_el_archivo(tmp_path: Path):
    ruta = tmp_path / "c.toml"
    ruta.write_text(
        """
[vigilancia]
horas = ["08:00", "12:00", "16:00"]
dias = ["lun", "mie", "vie"]
hasta = "17:00"
festivos = ["2026-12-25"]
iniciar_con_interfaz = false
""",
        encoding="utf-8",
    )
    config = cargar_configuracion(ruta)
    assert config.vigilancia.horas == ["08:00", "12:00", "16:00"] and config.vigilancia.dias == ["lun", "mie", "vie"]
    assert config.vigilancia.hasta == "17:00" and config.vigilancia.festivos == ["2026-12-25"]
    assert config.vigilancia.iniciar_con_interfaz is False
