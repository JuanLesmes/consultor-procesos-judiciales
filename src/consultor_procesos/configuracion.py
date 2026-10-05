"""Configuración por archivo TOML con valores predeterminados conservadores.

Los valores por defecto están pensados para ser amables con el portal: 12 solicitudes por
minuto como máximo, pausa entre radicados, reintentos con retroceso, cortacircuito y un
presupuesto diario de 400 solicitudes. Súbalos solo si tiene una razón concreta.

En el servidor casi todo funciona con los valores por defecto; lo que cambia de una
instalación a otra va en variables de entorno (archivo `.env` del despliegue):

* `CONSULTOR_CONFIG`: ruta del archivo TOML (opcional).
* `CONSULTOR_CONTACTO`: correo con el que el programa se identifica ante la Rama Judicial.
* `CONSULTOR_USUARIOS`: usuarios de la interfaz (ver `consultor-procesos crear-usuario`).
* `CONSULTOR_SMTP_CONTRASENA`: contraseña del correo, si se activan los avisos por correo.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .enlaces import URL_BASE_CPNU

NOMBRE_ARCHIVO_PREDETERMINADO = "consultor_procesos.toml"
VARIABLE_CONFIG = "CONSULTOR_CONFIG"
VARIABLE_CONTACTO = "CONSULTOR_CONTACTO"


@dataclass
class ConfigGeneral:
    base_datos: str = "consultor_procesos.sqlite"
    # "{version}" se reemplaza por la versión del programa. CONSULTOR_CONTACTO, si está definida, manda.
    agente_usuario: str = (
        "ConsultorDeProcesos/{version} (vigilancia de radicados propios; "
        "configure CONSULTOR_CONTACTO con un correo de contacto)"
    )
    url_base: str = URL_BASE_CPNU
    nivel_registro: str = "INFO"
    archivo_registro: str | None = None
    directorio_documentos: str = "documentos"


@dataclass
class ConfigCortesia:
    solicitudes_por_minuto: float = 12.0
    rafaga: int = 1
    pausa_entre_procesos_segundos: float = 3.0
    intentos_max: int = 4
    espera_base_segundos: float = 2.0
    factor_retroceso: float = 2.0
    espera_maxima_segundos: float = 60.0
    retry_after_maximo_segundos: float = 300.0
    umbral_fallos_cortacircuito: int = 5
    segundos_cortacircuito_abierto: float = 300.0
    presupuesto_diario_solicitudes: int = 400
    tiempo_espera_segundos: float = 20.0
    hora_inicio: int = 0
    hora_fin: int = 24


@dataclass
class ConfigVerificacion:
    dias_gracia: int = 5
    horas_refresco_completo: int = 24
    max_paginas_inicial: int = 20
    palabras_clave: list[str] = field(default_factory=lambda: ["AUTO"])
    notificar_existentes: bool = False
    listar_documentos_de_autos: bool = True


@dataclass
class ConfigPublicaciones:
    habilitado: bool = True
    tipos: list[str] = field(
        default_factory=lambda: [
            "Notificaciones por Estados",
            "Notificaciones por Aviso",
            "Traslados especiales y ordinarios",
            "Autos masivo",
        ]
    )
    horas_entre_revisiones: float = 20.0
    dias_ventana_inicial: int = 7
    analizar_pdf: bool = True
    analizar_detalle: bool = True
    max_documentos_por_publicacion: int = 3
    max_paginas: int = 3
    solicitudes_por_minuto: float = 4.0
    pausa_entre_despachos_segundos: float = 5.0
    tiempo_espera_segundos: float = 90.0
    instancia_portlet: str = "BIyXQFHVaYaq"


@dataclass
class ConfigVigilancia:
    # Horario: los autos se registran en horario laboral, así que se verifica a horas fijas de
    # lunes a viernes y nada después de `hasta`. Con `horas = []` se vuelve al modo por intervalo.
    horas: list[str] = field(default_factory=lambda: ["07:00", "10:00", "13:00", "17:00"])
    dias: list[str] = field(default_factory=lambda: ["lun", "mar", "mie", "jue", "vie"])
    hasta: str = "18:00"
    festivos: list[str] = field(default_factory=list)
    fluctuacion_minutos: float = 5.0
    iniciar_con_interfaz: bool = True
    intervalo_minutos: float = 240.0
    jitter_fraccion: float = 0.2


@dataclass
class ConfigWeb:
    host: str = "127.0.0.1"
    puerto: int = 8770
    segundos_actualizacion: int = 30


@dataclass
class ConfigCorreo:
    habilitado: bool = False
    servidor: str = "smtp.gmail.com"
    puerto: int = 587
    usuario: str = ""
    contrasena_env: str = "CONSULTOR_SMTP_CONTRASENA"
    remitente: str = ""
    destinatarios: list[str] = field(default_factory=list)
    usar_tls: bool = True
    solo_autos: bool = True


@dataclass
class ConfigWebhook:
    habilitado: bool = False
    url: str = ""
    solo_autos: bool = False


@dataclass
class ConfigNotificaciones:
    consola: bool = True
    solo_autos_consola: bool = False
    archivo_jsonl: str | None = "novedades.jsonl"
    correo: ConfigCorreo = field(default_factory=ConfigCorreo)
    webhook: ConfigWebhook = field(default_factory=ConfigWebhook)


@dataclass
class Configuracion:
    general: ConfigGeneral = field(default_factory=ConfigGeneral)
    cortesia: ConfigCortesia = field(default_factory=ConfigCortesia)
    verificacion: ConfigVerificacion = field(default_factory=ConfigVerificacion)
    vigilancia: ConfigVigilancia = field(default_factory=ConfigVigilancia)
    notificaciones: ConfigNotificaciones = field(default_factory=ConfigNotificaciones)
    web: ConfigWeb = field(default_factory=ConfigWeb)
    publicaciones: ConfigPublicaciones = field(default_factory=ConfigPublicaciones)


def _poblar(tipo: type, datos: dict[str, Any]) -> Any:
    """Construye el dataclass `tipo` a partir de un diccionario, ignorando claves desconocidas."""
    instancia = tipo()
    for campo in fields(tipo):
        if campo.name not in datos:
            continue
        valor = datos[campo.name]
        actual = getattr(instancia, campo.name)
        if is_dataclass(actual) and isinstance(valor, dict):
            setattr(instancia, campo.name, _poblar(type(actual), valor))
        else:
            setattr(instancia, campo.name, valor)
    return instancia


def cargar_configuracion(ruta: str | Path | None = None) -> Configuracion:
    """Carga la configuración.

    Sin ruta usa CONSULTOR_CONFIG o, si no está definida, `consultor_procesos.toml` cuando
    existe; si no hay archivo, los valores por defecto.
    """
    if ruta is None and os.environ.get(VARIABLE_CONFIG, "").strip():
        candidata = Path(os.environ[VARIABLE_CONFIG].strip())
        return _cargar_archivo(candidata) if candidata.exists() else Configuracion()
    if ruta is None:
        candidata = Path(NOMBRE_ARCHIVO_PREDETERMINADO)
        if not candidata.exists():
            return Configuracion()
        ruta = candidata
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo de configuración {ruta}")
    return _cargar_archivo(ruta)


def _cargar_archivo(ruta: Path) -> Configuracion:
    with ruta.open("rb") as archivo:
        datos = tomllib.load(archivo)
    return _poblar(Configuracion, datos)


def agente_usuario(config: Configuracion) -> str:
    """El User-Agent con el que el programa se identifica ante la Rama Judicial."""
    contacto = os.environ.get(VARIABLE_CONTACTO, "").strip()
    if contacto:
        return f"ConsultorDeProcesos/{__version__} (vigilancia de radicados propios; contacto: {contacto})"
    return config.general.agente_usuario.replace("{version}", __version__)


def tiene_contacto(agente: str) -> bool:
    return "@" in agente


def contrasena_correo(config: ConfigCorreo) -> str:
    """La contraseña SMTP nunca va en el TOML: se lee de la variable de entorno configurada."""
    return os.environ.get(config.contrasena_env, "") if config.contrasena_env else ""


CONFIGURACION_EJEMPLO = """# Configuración de Consultor de Procesos.
# Todos los valores son opcionales: lo que no se indique toma el valor predeterminado.

[general]
base_datos = "consultor_procesos.sqlite"
# Identifíquese ante la Rama Judicial: un agente honesto con contacto es la mejor defensa
# contra bloqueos, porque permite que el administrador le escriba antes de vetarlo.
# En el servidor es más cómodo definir CONSULTOR_CONTACTO en el .env, que tiene prioridad.
agente_usuario = "ConsultorDeProcesos/{version} (vigilancia de radicados propios; contacto: su-correo@ejemplo.com)"
nivel_registro = "INFO"
# archivo_registro = "consultor_procesos.log"
directorio_documentos = "documentos"   # caché local de los PDF descargados

[cortesia]
solicitudes_por_minuto = 12        # ritmo sostenido máximo (una cada 5 s)
rafaga = 1                         # solicitudes inmediatas permitidas antes de esperar
pausa_entre_procesos_segundos = 3  # respiro entre un radicado y el siguiente
intentos_max = 4                   # intentos totales ante 429/5xx/tiempo de espera
espera_base_segundos = 2           # retroceso exponencial: 2, 4, 8... con fluctuación
espera_maxima_segundos = 60
retry_after_maximo_segundos = 300  # si el servidor pide esperar más que esto, se desiste
umbral_fallos_cortacircuito = 5    # fallos seguidos para suspender las consultas
segundos_cortacircuito_abierto = 300
presupuesto_diario_solicitudes = 400
tiempo_espera_segundos = 20
hora_inicio = 0                    # ventana horaria del modo por intervalo (0 y 24 = sin restricción)
hora_fin = 24

[verificacion]
dias_gracia = 5                # si la última actuación es reciente, se releen actuaciones aunque la fecha no cambie
horas_refresco_completo = 24   # cada cuántas horas se releen actuaciones aunque nada haya cambiado
max_paginas_inicial = 20       # tope de páginas (de 40) por lectura; las siguientes leen hasta empalmar con lo conocido
palabras_clave = ["AUTO"]      # añada p. ej. "SENTENCIA", "FIJACION ESTADO" si quiere alertas de más tipos
notificar_existentes = false   # true para recibir también las actuaciones ya existentes al agregar un radicado
listar_documentos_de_autos = true   # al detectar un auto con documentos, pedir la lista (1 solicitud) para enlazar el PDF

[publicaciones]
# Micrositios de los despachos (portal Publicaciones Procesales): estados, avisos, traslados...
habilitado = true
tipos = ["Notificaciones por Estados", "Notificaciones por Aviso", "Traslados especiales y ordinarios", "Autos masivo"]
horas_entre_revisiones = 20      # cada despacho se revisa a lo sumo una vez en este lapso (cada consulta pesa ~1 MB)
dias_ventana_inicial = 7         # días hacia atrás que se miran la primera vez
analizar_pdf = true              # descargar los PDF (estado, autos) y buscar el radicado dentro
analizar_detalle = true          # si el listado no trae documentos, abrir el detalle (muchos despachos ponen allí un PDF por auto)
max_documentos_por_publicacion = 3
max_paginas = 3
solicitudes_por_minuto = 4
pausa_entre_despachos_segundos = 5

[vigilancia]
# Los autos se registran en horario laboral: se verifica a horas fijas, de lunes a viernes.
horas = ["07:00", "10:00", "13:00", "17:00"]   # 4 veces al día; puede añadir p. ej. "15:00"
dias = ["lun", "mar", "mie", "jue", "vie"]
hasta = "18:00"                # después de esta hora no se ejecuta ninguna verificación atrasada
fluctuacion_minutos = 5        # cada verificación arranca hasta 5 min después de la hora, al azar
festivos = []                  # fechas "AAAA-MM-DD" en las que no se verifica
iniciar_con_interfaz = true    # al arrancar el servidor ('web'), la vigilancia arranca sola con este horario
intervalo_minutos = 240        # solo si horas = []: verificar cada tantos minutos
jitter_fraccion = 0.2          # +/- 20 % aleatorio sobre el intervalo

[web]
host = "127.0.0.1"          # en el contenedor se usa --host 0.0.0.0 detrás de Caddy (HTTPS)
puerto = 8770
segundos_actualizacion = 30

[notificaciones]
consola = true
solo_autos_consola = false
archivo_jsonl = "novedades.jsonl"   # "" para desactivar

[notificaciones.correo]
habilitado = false
servidor = "smtp.gmail.com"
puerto = 587
usuario = "su-correo@gmail.com"
contrasena_env = "CONSULTOR_SMTP_CONTRASENA"   # la contraseña se lee de esta variable de entorno
remitente = "su-correo@gmail.com"
destinatarios = ["destino@ejemplo.com"]
usar_tls = true
solo_autos = true

[notificaciones.webhook]
habilitado = false
url = ""
solo_autos = false
"""


def escribir_ejemplo(ruta: str | Path = NOMBRE_ARCHIVO_PREDETERMINADO, sobrescribir: bool = False) -> Path:
    destino = Path(ruta)
    if destino.exists() and not sobrescribir:
        raise FileExistsError(f"Ya existe {destino}; use --sobrescribir para reemplazarlo.")
    destino.write_text(CONFIGURACION_EJEMPLO, encoding="utf-8")
    return destino
