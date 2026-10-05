"""Interfaz de línea de comandos del Consultor de Procesos."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import signal
import sys
import tomllib
from collections.abc import Callable, Iterable
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

from . import __version__
from .adaptadores.cpnu.cliente import ClienteCPNU
from .adaptadores.notificacion.archivo import NotificadorArchivoJSONL
from .adaptadores.notificacion.compuesto import NotificadorCompuesto
from .adaptadores.notificacion.consola import NotificadorConsola
from .adaptadores.notificacion.correo import ConfiguracionCorreo, NotificadorCorreo
from .adaptadores.notificacion.formato import linea_novedad, linea_publicacion, serializar_consulta
from .adaptadores.notificacion.webhook import NotificadorWebhook
from .adaptadores.persistencia.sqlite import RepositorioSQLite
from .adaptadores.publicaciones.cliente import ClientePublicaciones
from .adaptadores.web.seguridad import LONGITUD_MINIMA_CLAVE, VARIABLE_USUARIOS, Autenticador, generar_hash
from .adaptadores.web.servidor import AplicacionWeb, ServidorWeb
from .aplicacion.horario import Horario
from .aplicacion.planificador import Planificador
from .aplicacion.servicio_publicaciones import OpcionesPublicaciones, ServicioPublicaciones
from .aplicacion.servicio_vigilancia import OpcionesVerificacion, ServicioVigilancia
from .configuracion import (
    VARIABLE_CONTACTO,
    Configuracion,
    agente_usuario,
    cargar_configuracion,
    contrasena_correo,
    escribir_ejemplo,
    tiene_contacto,
)
from .dominio.errores import (
    ErrorConsultor,
    FuenteNoDisponible,
    PresupuestoAgotado,
    ProcesoNoEncontrado,
    RadicadoInvalido,
)
from .dominio.modelos import ConsultaProceso, ResultadoPublicaciones, ResultadoVerificacion
from .dominio.puertos import FuenteProcesos, FuentePublicaciones, Notificador, Repositorio
from .dominio.reglas import DetectorAutos, validar_radicado
from .enlaces import TIPOS_PUBLICACION
from .infraestructura.cortesia import (
    Cortacircuito,
    LimitadorTasa,
    PoliticaReintentos,
    PresupuestoDiario,
    VentanaHoraria,
)
from .infraestructura.registro import configurar_registro

log = logging.getLogger(__name__)

FabricaFuente = Callable[[Configuracion, Repositorio], FuenteProcesos]
FabricaPublicaciones = Callable[[Configuracion, Repositorio], "FuentePublicaciones | None"]

CODIGO_OK = 0
CODIGO_USO = 1
CODIGO_FUENTE = 2
CODIGO_NO_ENCONTRADO = 3
HOSTS_LOCALES = frozenset({"127.0.0.1", "localhost", "::1"})


# --- construcción de componentes -------------------------------------------------------------


def construir_fuente(config: Configuracion, repositorio: Repositorio) -> ClienteCPNU:
    c = config.cortesia
    return ClienteCPNU(
        agente_usuario=agente_usuario(config),
        url_base=config.general.url_base,
        tiempo_espera=c.tiempo_espera_segundos,
        limitador=LimitadorTasa(c.solicitudes_por_minuto, rafaga=c.rafaga),
        reintentos=PoliticaReintentos(
            intentos_max=c.intentos_max,
            espera_base=c.espera_base_segundos,
            factor=c.factor_retroceso,
            espera_maxima=c.espera_maxima_segundos,
            retry_after_maximo=c.retry_after_maximo_segundos,
        ),
        cortacircuito=Cortacircuito(
            umbral_fallos=c.umbral_fallos_cortacircuito,
            segundos_abierto=c.segundos_cortacircuito_abierto,
        ),
        presupuesto=PresupuestoDiario(c.presupuesto_diario_solicitudes, repositorio),
    )


def construir_fuente_publicaciones(config: Configuracion, repositorio: Repositorio) -> ClientePublicaciones | None:
    p = config.publicaciones
    if not p.habilitado:
        return None
    c = config.cortesia
    return ClientePublicaciones(
        agente_usuario=agente_usuario(config),
        instancia_portlet=p.instancia_portlet,
        tiempo_espera=p.tiempo_espera_segundos,
        limitador=LimitadorTasa(p.solicitudes_por_minuto, rafaga=1),
        reintentos=PoliticaReintentos(
            intentos_max=c.intentos_max,
            espera_base=c.espera_base_segundos,
            factor=c.factor_retroceso,
            espera_maxima=c.espera_maxima_segundos,
            retry_after_maximo=c.retry_after_maximo_segundos,
        ),
        cortacircuito=Cortacircuito(
            umbral_fallos=c.umbral_fallos_cortacircuito,
            segundos_abierto=c.segundos_cortacircuito_abierto,
        ),
        presupuesto=PresupuestoDiario(c.presupuesto_diario_solicitudes, repositorio),
    )


def tipos_publicacion_configurados(nombres: list[str]) -> tuple[int, ...]:
    ids: list[int] = []
    for nombre in nombres:
        identificador = TIPOS_PUBLICACION.get(nombre)
        if identificador is None:
            log.warning("Tipo de publicación desconocido en la configuración: %r (se ignora).", nombre)
        elif identificador not in ids:
            ids.append(identificador)
    return tuple(ids) or OpcionesPublicaciones().tipos


def construir_notificador(config: Configuracion, salida: TextIO) -> NotificadorCompuesto:
    n = config.notificaciones
    notificadores: list[Notificador] = []
    if n.consola:
        notificadores.append(NotificadorConsola(salida=salida, solo_autos=n.solo_autos_consola))
    if n.archivo_jsonl:
        notificadores.append(NotificadorArchivoJSONL(n.archivo_jsonl))
    if n.correo.habilitado:
        cfg = n.correo
        notificadores.append(
            NotificadorCorreo(
                ConfiguracionCorreo(
                    servidor=cfg.servidor,
                    puerto=cfg.puerto,
                    usuario=cfg.usuario,
                    contrasena=contrasena_correo(cfg),
                    remitente=cfg.remitente or cfg.usuario,
                    destinatarios=tuple(cfg.destinatarios),
                    usar_tls=cfg.usar_tls,
                    solo_autos=cfg.solo_autos,
                )
            )
        )
    if n.webhook.habilitado and n.webhook.url:
        notificadores.append(NotificadorWebhook(n.webhook.url, solo_autos=n.webhook.solo_autos))
    return NotificadorCompuesto(notificadores)


def construir_servicio(
    config: Configuracion,
    repositorio: Repositorio,
    fuente: FuenteProcesos,
    salida: TextIO,
    fuente_publicaciones: FuentePublicaciones | None = None,
) -> ServicioVigilancia:
    v = config.verificacion
    opciones = OpcionesVerificacion(
        dias_gracia=v.dias_gracia,
        horas_refresco_completo=v.horas_refresco_completo,
        max_paginas_inicial=v.max_paginas_inicial,
        pausa_entre_procesos_segundos=config.cortesia.pausa_entre_procesos_segundos,
        notificar_existentes=v.notificar_existentes,
        listar_documentos_de_autos=v.listar_documentos_de_autos,
    )
    directorio = config.general.directorio_documentos
    notificador = construir_notificador(config, salida)
    publicaciones = None
    if fuente_publicaciones is not None:
        p = config.publicaciones
        publicaciones = ServicioPublicaciones(
            fuente_publicaciones,
            repositorio,
            notificador,
            opciones=OpcionesPublicaciones(
                tipos=tipos_publicacion_configurados(p.tipos),
                horas_entre_revisiones=p.horas_entre_revisiones,
                dias_ventana_inicial=p.dias_ventana_inicial,
                analizar_pdf=p.analizar_pdf,
                max_documentos_por_publicacion=p.max_documentos_por_publicacion,
                max_paginas=p.max_paginas,
                pausa_entre_despachos_segundos=p.pausa_entre_despachos_segundos,
            ),
        )
    return ServicioVigilancia(
        fuente=fuente,
        repositorio=repositorio,
        notificador=notificador,
        detector=DetectorAutos(v.palabras_clave),
        opciones=opciones,
        directorio_documentos=Path(directorio) if directorio else None,
        publicaciones=publicaciones,
    )


def construir_horario(config: Configuracion) -> Horario | None:
    """El horario de verificación (horas fijas en días hábiles); None si se configuró por intervalo."""
    v = config.vigilancia
    if not v.horas:
        return None
    return Horario.desde_texto(v.horas, v.dias, v.hasta, v.festivos)


def ultima_verificacion_registrada(repositorio: Repositorio) -> datetime | None:
    filas = repositorio.listar_verificaciones(limite=1)
    return filas[0]["momento"] if filas else None


def construir_planificador(
    config: Configuracion,
    ciclo: Callable[[], None],
    repositorio: Repositorio,
    intervalo_minutos: float | None = None,
) -> Planificador:
    """Por horario (predeterminado) o por intervalo si así se configuró o se pidió por línea de comandos."""
    v = config.vigilancia
    horario = None if intervalo_minutos else construir_horario(config)
    if horario is not None:
        return Planificador(
            ciclo,
            horario=horario,
            ultima_ejecucion=lambda: ultima_verificacion_registrada(repositorio),
            fluctuacion_segundos=max(0.0, v.fluctuacion_minutos) * 60,
        )
    return Planificador(
        ciclo,
        intervalo_segundos=(intervalo_minutos or v.intervalo_minutos) * 60,
        jitter_fraccion=v.jitter_fraccion,
        ventana=VentanaHoraria(config.cortesia.hora_inicio, config.cortesia.hora_fin),
    )


# --- presentación ---------------------------------------------------------------------------


def _fmt(valor: object) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M")
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)


def _tabla(salida: TextIO, encabezados: list[str], filas: Iterable[Iterable[object]]) -> None:
    filas_texto = [[_fmt(c) for c in fila] for fila in filas]
    anchos = [len(e) for e in encabezados]
    for fila in filas_texto:
        for i, celda in enumerate(fila):
            anchos[i] = max(anchos[i], len(celda))

    def linea(celdas: list[str]) -> str:
        return "  ".join(c.ljust(anchos[i]) for i, c in enumerate(celdas)).rstrip()

    print(linea(encabezados), file=salida)
    print("  ".join("-" * a for a in anchos), file=salida)
    for fila in filas_texto:
        print(linea(fila), file=salida)
    if not filas_texto:
        print("(sin registros)", file=salida)


def imprimir_resultados(
    resultados: list[ResultadoVerificacion],
    salida: TextIO,
    publicaciones: list[ResultadoPublicaciones] | None = None,
) -> None:
    _tabla(
        salida,
        ["Radicado", "Estado", "Nuevas", "Autos", "Public.", "Solic.", "Detalle"],
        [
            [r.radicado, r.estado.value, len(r.novedades), len(r.autos), len(r.publicaciones), r.solicitudes, r.mensaje]
            for r in resultados
        ],
    )
    if publicaciones:
        print("Publicaciones de los despachos (estados, avisos, traslados):", file=salida)
        _tabla(
            salida,
            ["Despacho", "Estado", "Nuevas", "Coinc.", "Solic.", "Detalle"],
            [[p.despacho_codigo, p.estado.value, p.nuevas, len(p.coincidencias), p.solicitudes, p.mensaje] for p in publicaciones],
        )
    total = sum(r.solicitudes for r in resultados)
    autos = sum(len(r.autos) for r in resultados if not r.es_linea_base)
    print(f"Total: {len(resultados)} radicado(s), {total} solicitud(es) a la fuente, {autos} auto(s) nuevo(s).", file=salida)


def imprimir_consulta(consulta: ConsultaProceso, salida: TextIO, solo_autos: bool) -> None:
    print(f"Radicado {consulta.radicado}", file=salida)
    for proceso in consulta.procesos:
        print(f"  Despacho: {proceso.despacho}", file=salida)
        if proceso.departamento:
            print(f"  Departamento: {proceso.departamento}", file=salida)
        if proceso.sujetos:
            print(f"  Sujetos: {proceso.sujetos}", file=salida)
        print(f"  Fecha de radicación: {_fmt(proceso.fecha_proceso)}", file=salida)
        print(f"  Última actuación: {_fmt(proceso.fecha_ultima_actuacion)}", file=salida)
        if proceso.es_privado:
            print("  (proceso privado: la fuente no publica actuaciones)", file=salida)
    for detalle in consulta.detalles:
        print(f"  Ponente: {detalle.ponente or '-'}", file=salida)
        print(f"  Tipo / clase: {detalle.tipo_proceso or '-'} / {detalle.clase_proceso or '-'}", file=salida)
        print(f"  Ubicación: {detalle.ubicacion or '-'}", file=salida)
    novedades = consulta.autos if solo_autos else consulta.novedades
    print(file=salida)
    print(f"Actuaciones ({len(consulta.actuaciones)} en total, {len(consulta.autos)} auto(s)):", file=salida)
    for novedad in novedades:
        print(linea_novedad(novedad), file=salida)
    if not novedades:
        print("  (ninguna)", file=salida)


# --- analizador de argumentos ------------------------------------------------------------------


def construir_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="consultor-procesos",
        description="Vigilancia cortés de procesos judiciales en la Rama Judicial de Colombia (CPNU).",
    )
    analizador.add_argument("--config", help="Archivo TOML de configuración (por defecto consultor_procesos.toml si existe).")
    analizador.add_argument("--bd", help="Ruta de la base de datos SQLite (anula general.base_datos).")
    analizador.add_argument("-v", "--verboso", action="store_true", help="Registro detallado (DEBUG).")
    analizador.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = analizador.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("iniciar-config", help="Escribe un archivo de configuración de ejemplo.")
    s.add_argument("--ruta", default="consultor_procesos.toml")
    s.add_argument("--sobrescribir", action="store_true")

    s = sub.add_parser("agregar", help="Agrega un radicado (23 dígitos) a la vigilancia.")
    s.add_argument("radicado")
    s.add_argument("--alias", help="Nombre corto para reconocer el proceso.")

    s = sub.add_parser("quitar", help="Quita un radicado de la vigilancia y borra su historial local.")
    s.add_argument("radicado")

    sub.add_parser("listar", help="Lista los radicados vigilados.")

    s = sub.add_parser("verificar", help="Verifica una vez los radicados vigilados y notifica novedades.")
    s.add_argument("--radicado", action="append", help="Limitar a este radicado (repetible).")
    s.add_argument("--notificar-existentes", action="store_true", help="Notificar también la línea base.")

    s = sub.add_parser("vigilar", help="Verifica según el horario configurado (o cada tanto) hasta que se interrumpa (Ctrl+C).")
    s.add_argument("--intervalo-minutos", type=float, help="Usar un intervalo fijo en vez del horario de vigilancia.horas.")
    s.add_argument("--ciclos", type=int, help="Número máximo de ciclos (por defecto, ilimitado).")

    s = sub.add_parser("consultar", help="Consulta puntual de un radicado, sin guardar nada.")
    s.add_argument("radicado")
    s.add_argument("--json", action="store_true", help="Salida en JSON.")
    s.add_argument("--solo-autos", action="store_true", help="Mostrar solo las actuaciones que son autos.")
    s.add_argument("--max-paginas", type=int, default=5, help="Páginas de 40 actuaciones a leer (por defecto 5).")
    s.add_argument("--sin-detalle", action="store_true", help="No pedir la ficha de detalle (ahorra una solicitud).")
    s.add_argument("--salida", help="Escribir el resultado en este archivo (UTF-8) en vez de mostrarlo en pantalla.")

    s = sub.add_parser("autos", help="Muestra los autos guardados localmente para un radicado.")
    s.add_argument("radicado")
    s.add_argument("--todas", action="store_true", help="Mostrar todas las actuaciones, no solo autos.")

    s = sub.add_parser("historial", help="Muestra las últimas verificaciones realizadas.")
    s.add_argument("--radicado")
    s.add_argument("--limite", type=int, default=20)

    s = sub.add_parser("publicaciones", help="Estados, avisos y traslados de los despachos que mencionan sus radicados.")
    s.add_argument("--radicado", help="Limitar a este radicado.")
    s.add_argument("--despacho", help="Listar todas las publicaciones vistas de este despacho (código de 12 dígitos).")
    s.add_argument("--revisar", action="store_true", help="Consultar el portal ahora, sin esperar el intervalo.")
    s.add_argument("--limite", type=int, default=30)

    s = sub.add_parser("web", help="Servidor web: interfaz (novedades, autos, documentos) y vigilancia automática.")
    s.add_argument("--host", help="Dirección de escucha (por defecto web.host, 127.0.0.1; en el contenedor 0.0.0.0).")
    s.add_argument("--puerto", type=int, help="Puerto (por defecto web.puerto, 8770; 0 elige uno libre).")
    s.add_argument("--vigilar", action="store_true", help="Arrancar la vigilancia al iniciar aunque vigilancia.iniciar_con_interfaz sea false.")
    s.add_argument("--sin-vigilar", action="store_true", help="No arrancar la vigilancia automática.")
    s.add_argument("--proxy", action="store_true", help="Detrás de un proxy (Caddy): tomar la IP del cliente de X-Forwarded-For.")
    s.add_argument(
        "--sin-autenticacion",
        action="store_true",
        help=f"Permitir escuchar fuera de 127.0.0.1 sin usuarios ({VARIABLE_USUARIOS}); solo para una red interna.",
    )

    s = sub.add_parser("crear-usuario", help=f"Genera la línea de un usuario para {VARIABLE_USUARIOS} (no guarda nada).")
    s.add_argument("nombre", help="Nombre de usuario (letras, números, punto, guion, arroba).")
    s.add_argument("--clave-stdin", action="store_true", help="Leer la contraseña de la entrada estándar en vez de pedirla.")

    s = sub.add_parser("respaldar", help="Copia consistente de la base de datos, aunque el servidor esté en marcha.")
    s.add_argument("--destino", default="respaldos", help="Carpeta de los respaldos (por defecto ./respaldos).")
    s.add_argument("--conservar", type=int, default=14, help="Cuántos respaldos conservar (por defecto 14).")
    return analizador


# --- comandos ------------------------------------------------------------------------------------


def _cmd_agregar(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    vigilado = servicio.agregar(args.radicado, alias=args.alias)
    print(f"Radicado {vigilado.radicado} en vigilancia" + (f" (alias: {vigilado.alias})" if vigilado.alias else "") + ".", file=salida)
    print("La primera verificación registrará la línea base sin notificar.", file=salida)
    return CODIGO_OK


def _cmd_quitar(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    if servicio.quitar(args.radicado):
        print(f"Radicado {args.radicado} retirado de la vigilancia.", file=salida)
        return CODIGO_OK
    print(f"El radicado {args.radicado} no estaba en vigilancia.", file=salida)
    return CODIGO_NO_ENCONTRADO


def _cmd_listar(servicio: ServicioVigilancia, salida: TextIO) -> int:
    vigilados = servicio.listar(solo_activos=False)
    _tabla(
        salida,
        ["Radicado", "Alias", "Última actuación", "Última verificación", "Línea base", "Activo"],
        [
            [
                v.radicado,
                v.alias or "",
                v.fecha_ultima_actuacion,
                v.ultima_verificacion,
                "sí" if v.inicializado else "pendiente",
                "sí" if v.activo else "no",
            ]
            for v in vigilados
        ],
    )
    return CODIGO_OK


def _cmd_verificar(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    if args.notificar_existentes:
        servicio.opciones.notificar_existentes = True
    if not servicio.listar():
        print("No hay radicados en vigilancia. Use 'agregar' primero.", file=salida)
        return CODIGO_OK
    resultados = servicio.verificar_todos(args.radicado)
    imprimir_resultados(resultados, salida, servicio.ultimos_resultados_publicaciones)
    if any(r.estado.value == "OMITIDO" for r in resultados):
        return CODIGO_FUENTE
    return CODIGO_OK


def _cmd_vigilar(
    args: argparse.Namespace, config: Configuracion, servicio: ServicioVigilancia, repositorio: Repositorio, salida: TextIO
) -> int:
    def ciclo() -> None:
        print(f"--- Ciclo de verificación {datetime.now():%Y-%m-%d %H:%M} ---", file=salida)
        imprimir_resultados(servicio.verificar_todos(), salida, servicio.ultimos_resultados_publicaciones)

    planificador = construir_planificador(config, ciclo, repositorio, args.intervalo_minutos)
    cantidad = len(servicio.listar())
    print(f"Vigilando {cantidad} radicado(s) {planificador.descripcion()}. Ctrl+C para detener.", file=salida)
    try:
        planificador.ejecutar(max_ciclos=args.ciclos)
    except KeyboardInterrupt:
        print("\nVigilancia detenida por el usuario.", file=salida)
    return CODIGO_OK


def _escribir_consulta(consulta: ConsultaProceso, destino: TextIO, args: argparse.Namespace) -> None:
    if args.json:
        json.dump(serializar_consulta(consulta), destino, ensure_ascii=False, indent=2)
        print(file=destino)
    else:
        imprimir_consulta(consulta, destino, args.solo_autos)


def _cmd_consultar(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    consulta = servicio.consultar(args.radicado, incluir_detalle=not args.sin_detalle, max_paginas=args.max_paginas)
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as archivo:
            _escribir_consulta(consulta, archivo, args)
        print(f"Resultado escrito en {args.salida}.", file=salida)
    else:
        _escribir_consulta(consulta, salida, args)
    return CODIGO_OK


def _cmd_autos(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    novedades = servicio.actuaciones_registradas(args.radicado) if args.todas else servicio.autos_registrados(args.radicado)
    print(f"Radicado {args.radicado}: {len(novedades)} registro(s) en la base local.", file=salida)
    for novedad in novedades:
        print(linea_novedad(novedad), file=salida)
    return CODIGO_OK


def _cmd_publicaciones(args: argparse.Namespace, servicio: ServicioVigilancia, salida: TextIO) -> int:
    publicaciones = servicio.publicaciones
    if publicaciones is None:
        print("La revisión de publicaciones está desactivada (publicaciones.habilitado = false).", file=salida)
        return CODIGO_USO
    radicado = validar_radicado(args.radicado) if args.radicado else None
    if args.revisar:
        vigilados = [v for v in servicio.listar() if radicado is None or v.radicado == radicado]
        if not vigilados:
            print("No hay radicados en vigilancia para revisar.", file=salida)
            return CODIGO_OK
        resultados = publicaciones.revisar(vigilados, forzar=True)
        _tabla(
            salida,
            ["Despacho", "Estado", "Nuevas", "Coinc.", "Solic.", "Detalle"],
            [[p.despacho_codigo, p.estado.value, p.nuevas, len(p.coincidencias), p.solicitudes, p.mensaje] for p in resultados],
        )
        if any(p.estado.value == "OMITIDO" for p in resultados):
            return CODIGO_FUENTE
    if args.despacho:
        lista = publicaciones.publicaciones_despacho(args.despacho.strip(), limite=args.limite)
        print(f"Despacho {args.despacho}: {len(lista)} publicación(es) registradas localmente.", file=salida)
        for p in lista:
            fecha = p.fecha_publicacion.isoformat() if p.fecha_publicacion else "sin fecha"
            print(f"  {fecha} {p.tipo}: {p.titulo} -> {p.url_detalle}", file=salida)
        return CODIGO_OK
    coincidencias = publicaciones.coincidencias_recientes(radicado=radicado, limite=args.limite)
    print(f"{len(coincidencias)} publicación(es) mencionan radicados vigilados.", file=salida)
    for c in coincidencias:
        print(f"Radicado {c.radicado}" + ("" if c.revisada else "  [pendiente]"), file=salida)
        print(linea_publicacion(c), file=salida)
    return CODIGO_OK


def _cmd_historial(args: argparse.Namespace, repositorio: Repositorio, salida: TextIO) -> int:
    filas = repositorio.listar_verificaciones(args.radicado, limite=args.limite)
    _tabla(
        salida,
        ["Momento", "Radicado", "Estado", "Nuevas", "Autos", "Solic.", "Detalle"],
        [[f["momento"], f["radicado"], f["estado"], f["novedades"], f["autos"], f["solicitudes"], f["mensaje"]] for f in filas],
    )
    return CODIGO_OK


def avisos_de_configuracion(config: Configuracion) -> list[str]:
    """Lo que conviene corregir en la configuración; se muestra al arrancar y en el monitor."""
    avisos: list[str] = []
    if not tiene_contacto(agente_usuario(config)):
        avisos.append(
            f"Falta el correo de contacto ({VARIABLE_CONTACTO}): sin él, la Rama Judicial no podría avisar antes de bloquear el acceso."
        )
    correo = config.notificaciones.correo
    if correo.habilitado and not contrasena_correo(correo):
        avisos.append(f"El aviso por correo está activado pero falta la contraseña ({correo.contrasena_env}).")
    return avisos


def construir_aplicacion_web(
    config: Configuracion,
    servicio: ServicioVigilancia,
    repositorio: Repositorio,
    fuente: FuenteProcesos,
    autenticador: Autenticador | None = None,
) -> AplicacionWeb:
    def fabrica_planificador(ciclo: Callable[[], None]) -> Planificador:
        return construir_planificador(config, ciclo, repositorio)

    return AplicacionWeb(
        servicio=servicio,
        repositorio=repositorio,
        fabrica_planificador=fabrica_planificador,
        presupuesto=getattr(fuente, "presupuesto", None),
        segundos_actualizacion=config.web.segundos_actualizacion,
        horario=construir_horario(config),
        autenticador=autenticador,
        commit=os.environ.get("CONSULTOR_COMMIT", "").strip() or None,
        avisos_configuracion=avisos_de_configuracion(config),
    )


def _al_recibir_sigterm(signum: int, marco: object) -> None:
    # `docker stop` envía SIGTERM: se convierte en la misma salida ordenada que Ctrl+C.
    raise KeyboardInterrupt


def _cmd_web(
    args: argparse.Namespace,
    config: Configuracion,
    servicio: ServicioVigilancia,
    repositorio: Repositorio,
    fuente: FuenteProcesos,
    salida: TextIO,
) -> int:
    host = args.host or config.web.host
    puerto = args.puerto if args.puerto is not None else config.web.puerto
    try:
        autenticador = Autenticador.desde_entorno()
    except ValueError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return CODIGO_USO
    if not autenticador.activo and host not in HOSTS_LOCALES and not args.sin_autenticacion:
        print(
            f"Para escuchar en {host} hay que definir usuarios en {VARIABLE_USUARIOS} "
            "(genérelos con 'consultor-procesos crear-usuario NOMBRE'). "
            "Use --sin-autenticacion solo dentro de una red interna.",
            file=sys.stderr,
        )
        return CODIGO_USO
    aplicacion = construir_aplicacion_web(config, servicio, repositorio, fuente, autenticador)
    servidor = ServidorWeb(aplicacion, host=host, puerto=puerto, confiar_proxy=args.proxy)
    try:
        servidor.iniciar()
    except OSError as exc:
        print(
            f"No se pudo abrir el puerto {puerto} en {host}: {exc}. "
            "Probablemente ya hay otro servidor en ese puerto; deténgalo o use --puerto 0 para elegir uno libre.",
            file=sys.stderr,
        )
        return CODIGO_USO
    acceso = f"usuarios: {', '.join(autenticador.usuarios)}" if autenticador.activo else "sin autenticación"
    print(f"Servidor disponible en {servidor.url} ({acceso}). Ctrl+C para detener.", file=salida)
    for aviso in avisos_de_configuracion(config):
        log.warning(aviso)
    arrancar = args.vigilar or (config.vigilancia.iniciar_con_interfaz and not args.sin_vigilar)
    if arrancar and aplicacion.vigilante is not None:
        aplicacion.vigilante.iniciar()
        descripcion = construir_planificador(config, lambda: None, repositorio).descripcion()
        print(f"Vigilancia automática {descripcion}. Puede pausarla desde la interfaz.", file=salida)
    try:
        anterior = signal.signal(signal.SIGTERM, _al_recibir_sigterm)
    except ValueError:  # fuera del hilo principal (pruebas): no se puede instalar el manejador
        anterior = None
    try:
        servidor.servir_para_siempre()
    except KeyboardInterrupt:
        print("\nServidor detenido.", file=salida)
    finally:
        servidor.detener()
        if anterior is not None:
            signal.signal(signal.SIGTERM, anterior)
    return CODIGO_OK


def _cmd_crear_usuario(args: argparse.Namespace, salida: TextIO) -> int:
    nombre = args.nombre.strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", nombre):
        print("El nombre de usuario solo admite letras, números, punto, guion y arroba (máximo 64).", file=sys.stderr)
        return CODIGO_USO
    if args.clave_stdin:
        clave = sys.stdin.readline().rstrip("\r\n")
    else:
        clave = getpass.getpass("Contraseña: ")
        if getpass.getpass("Repita la contraseña: ") != clave:
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return CODIGO_USO
    if len(clave) < LONGITUD_MINIMA_CLAVE:
        print(f"La contraseña debe tener al menos {LONGITUD_MINIMA_CLAVE} caracteres.", file=sys.stderr)
        return CODIGO_USO
    print(f"{nombre}:{generar_hash(clave)}", file=salida)
    print(
        f"Copie la línea anterior en {VARIABLE_USUARIOS} del archivo .env (varios usuarios separados por comas) "
        "y reinicie el servidor.",
        file=sys.stderr,
    )
    return CODIGO_OK


def _cmd_respaldar(args: argparse.Namespace, repositorio: Repositorio, salida: TextIO) -> int:
    respaldar = getattr(repositorio, "respaldar", None)
    if not callable(respaldar):
        print("El repositorio actual no admite respaldos.", file=sys.stderr)
        return CODIGO_USO
    carpeta = Path(args.destino)
    ruta = respaldar(carpeta / f"consultor-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
    copias = sorted(carpeta.glob("consultor-*.sqlite"))
    for vieja in copias[: max(0, len(copias) - max(1, args.conservar))]:
        vieja.unlink()
    print(f"Respaldo escrito en {ruta} ({ruta.stat().st_size} bytes); se conservan los últimos {max(1, args.conservar)}.", file=salida)
    return CODIGO_OK


def _despachar(
    args: argparse.Namespace,
    config: Configuracion,
    servicio: ServicioVigilancia,
    repositorio: Repositorio,
    fuente: FuenteProcesos,
    salida: TextIO,
) -> int:
    if args.comando == "agregar":
        return _cmd_agregar(args, servicio, salida)
    if args.comando == "quitar":
        return _cmd_quitar(args, servicio, salida)
    if args.comando == "listar":
        return _cmd_listar(servicio, salida)
    if args.comando == "verificar":
        return _cmd_verificar(args, servicio, salida)
    if args.comando == "vigilar":
        return _cmd_vigilar(args, config, servicio, repositorio, salida)
    if args.comando == "consultar":
        return _cmd_consultar(args, servicio, salida)
    if args.comando == "autos":
        return _cmd_autos(args, servicio, salida)
    if args.comando == "historial":
        return _cmd_historial(args, repositorio, salida)
    if args.comando == "publicaciones":
        return _cmd_publicaciones(args, servicio, salida)
    if args.comando == "web":
        return _cmd_web(args, config, servicio, repositorio, fuente, salida)
    if args.comando == "respaldar":
        return _cmd_respaldar(args, repositorio, salida)
    raise ValueError(f"Comando desconocido: {args.comando}")


def main(
    argv: list[str] | None = None,
    *,
    fabrica_fuente: FabricaFuente | None = None,
    fabrica_publicaciones: FabricaPublicaciones | None = None,
    salida: TextIO | None = None,
) -> int:
    salida = salida or sys.stdout
    if salida is sys.stdout and hasattr(salida, "reconfigure"):
        # En Windows, la salida redirigida a un archivo usa la codificación regional (cp1252);
        # se fuerza UTF-8 para que el JSON y los textos con tildes sean legibles en cualquier programa.
        try:
            if salida.isatty():
                salida.reconfigure(errors="replace")
            else:
                salida.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, AttributeError):
            pass
    analizador = construir_analizador()
    args = analizador.parse_args(argv)

    if args.comando == "iniciar-config":
        try:
            ruta = escribir_ejemplo(args.ruta, sobrescribir=args.sobrescribir)
        except FileExistsError as exc:
            print(str(exc), file=sys.stderr)
            return CODIGO_USO
        print(f"Configuración de ejemplo escrita en {ruta}. Edite general.agente_usuario con su correo.", file=salida)
        return CODIGO_OK
    if args.comando == "crear-usuario":
        return _cmd_crear_usuario(args, salida)

    try:
        config = cargar_configuracion(args.config)
    except (FileNotFoundError, tomllib.TOMLDecodeError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return CODIGO_USO

    configurar_registro("DEBUG" if args.verboso else config.general.nivel_registro, config.general.archivo_registro)
    repositorio = RepositorioSQLite(args.bd or config.general.base_datos)
    fuente = (fabrica_fuente or construir_fuente)(config, repositorio)
    fuente_publicaciones = (fabrica_publicaciones or construir_fuente_publicaciones)(config, repositorio)
    servicio = construir_servicio(config, repositorio, fuente, salida, fuente_publicaciones)
    try:
        return _despachar(args, config, servicio, repositorio, fuente, salida)
    except RadicadoInvalido as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return CODIGO_USO
    except ProcesoNoEncontrado as exc:
        print(str(exc), file=sys.stderr)
        return CODIGO_NO_ENCONTRADO
    except (FuenteNoDisponible, PresupuestoAgotado) as exc:
        print(f"La fuente no está disponible en este momento: {exc}", file=sys.stderr)
        return CODIGO_FUENTE
    except ErrorConsultor as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return CODIGO_USO
    finally:
        for recurso in (fuente, fuente_publicaciones):
            cerrar = getattr(recurso, "cerrar", None)
            if callable(cerrar):
                cerrar()
        repositorio.cerrar()

