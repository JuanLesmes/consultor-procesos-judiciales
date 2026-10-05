"""Enlaces al portal oficial y a la API de la Consulta de Procesos Nacional Unificada.

El portal es una aplicación de una sola página sin enlaces profundos utilizables: existe la
ruta `/details-process/{idProceso}`, pero abierta directamente muestra la ficha vacía porque
solo se llena tras una búsqueda en la misma sesión (comprobado el 8 de septiembre de 2026).
Por eso el enlace "al portal" lleva a la página de consulta por número de radicación y la
interfaz local copia el radicado al portapapeles. Lo que sí se puede enlazar son los datos
en bruto de la API oficial, que responden en JSON a una navegación normal del navegador.
"""

URL_BASE_CPNU = "https://consultaprocesos.ramajudicial.gov.co:448/api/v2"
URL_PORTAL_CONSULTA = "https://consultaprocesos.ramajudicial.gov.co/Procesos/NumeroRadicacion"


def url_descarga_documento(id_documento: int) -> str:
    """Descarga directa del documento de una actuación desde la API oficial."""
    return f"{URL_BASE_CPNU}/Descarga/DocumentoActuacion/{int(id_documento)}"


def url_origen_ficha(id_proceso: int) -> str:
    """La ficha del proceso tal como la entrega la API oficial (JSON)."""
    return f"{URL_BASE_CPNU}/Proceso/Detalle/{int(id_proceso)}"


def url_origen_actuaciones(id_proceso: int, pagina: int = 1) -> str:
    """La lista de actuaciones tal como la entrega la API oficial (JSON, 40 por página)."""
    return f"{URL_BASE_CPNU}/Proceso/Actuaciones/{int(id_proceso)}?pagina={int(pagina)}"


# --- Publicaciones Procesales (micrositios de los despachos) ---------------------------------
URL_PUBLICACIONES = "https://publicacionesprocesales.ramajudicial.gov.co"
RUTA_PUBLICACIONES_INICIO = "/web/publicaciones-procesales/inicio"
PORTLET_PUBLICACIONES = "co_com_avanti_efectosProcesales_PublicacionesEfectosProcesalesPortletV2"
INSTANCIA_PORTLET_PUBLICACIONES = "BIyXQFHVaYaq"

# Tipos de publicación ("estructuras") observados en el portal en septiembre de 2026.
TIPOS_PUBLICACION: dict[str, int] = {
    "Notificaciones por Estados": 6098957,
    "Notificaciones por Aviso": 6098977,
    "Notificaciones": 6098981,
    "Traslados especiales y ordinarios": 6098965,
    "Autos masivo": 6098961,
    "Edictos": 6098953,
    "Fijaciones": 6098973,
    "Sentencias": 16709563,
    "Acciones de Tutela": 6098993,
    "Incidente de Desacato": 6099005,
    "Comunicaciones jurídicas": 6098985,
    "Control de legalidad": 6098989,
    "Entradas al despacho": 10498720,
    "Informes de Acumulación": 6099001,
    "Oficios": 6098969,
    "Remates": 6098997,
    "Reparto": 25326533,
    "Avisos": 9045223,
}


def nombre_tipo_publicacion(id_estructura: int | None) -> str:
    for nombre, identificador in TIPOS_PUBLICACION.items():
        if identificador == id_estructura:
            return nombre
    return str(id_estructura or "")
