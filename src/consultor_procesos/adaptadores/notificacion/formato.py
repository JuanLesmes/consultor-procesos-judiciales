"""Serialización y texto legible de eventos de novedades. Compartido por notificadores, CLI y web."""

from __future__ import annotations

from ...dominio.modelos import (
    Actuacion,
    CoincidenciaPublicacion,
    ConsultaProceso,
    Documento,
    EventoNovedades,
    Novedad,
    Publicacion,
)
from ...enlaces import URL_PORTAL_CONSULTA, url_descarga_documento


def serializar_actuacion(actuacion: Actuacion) -> dict:
    return {
        "id_registro": actuacion.id_registro,
        "id_proceso": actuacion.id_proceso,
        "radicado": actuacion.radicado,
        "consecutivo": actuacion.consecutivo,
        "fecha_actuacion": actuacion.fecha_actuacion.isoformat() if actuacion.fecha_actuacion else None,
        "fecha_registro": actuacion.fecha_registro.isoformat() if actuacion.fecha_registro else None,
        "fecha_inicial": actuacion.fecha_inicial.isoformat() if actuacion.fecha_inicial else None,
        "fecha_final": actuacion.fecha_final.isoformat() if actuacion.fecha_final else None,
        "actuacion": actuacion.actuacion,
        "anotacion": actuacion.anotacion,
        "con_documentos": actuacion.con_documentos,
    }


def serializar_documento(documento: Documento) -> dict:
    return {
        "id_documento": documento.id_documento,
        "id_registro": documento.id_registro,
        "nombre": documento.nombre,
        "fecha": documento.fecha.isoformat() if documento.fecha else None,
        "tipo": documento.tipo,
        "tamano": documento.tamano,
        "url_fuente": url_descarga_documento(documento.id_documento),
    }


def serializar_publicacion(publicacion: Publicacion) -> dict:
    return {
        "id_publicacion": publicacion.id_publicacion,
        "tipo": publicacion.tipo,
        "id_estructura": publicacion.id_estructura,
        "despacho_codigo": publicacion.despacho_codigo,
        "despacho": publicacion.despacho,
        "titulo": publicacion.titulo,
        "url_detalle": publicacion.url_detalle,
        "fecha_publicacion": publicacion.fecha_publicacion.isoformat() if publicacion.fecha_publicacion else None,
        "resumen": publicacion.resumen,
        "documentos": [{"etiqueta": d.etiqueta, "url": d.url} for d in publicacion.documentos],
        "departamento": publicacion.departamento,
        "municipio": publicacion.municipio,
        "entidad": publicacion.entidad,
        "especialidad": publicacion.especialidad,
        "analizada": publicacion.analizada,
        "visto_en": publicacion.visto_en.isoformat(timespec="seconds") if publicacion.visto_en else None,
    }


def serializar_coincidencia(coincidencia: CoincidenciaPublicacion) -> dict:
    return {
        "id": coincidencia.id,
        "radicado": coincidencia.radicado,
        "forma": coincidencia.forma,
        "donde": coincidencia.donde,
        "fragmento": coincidencia.fragmento,
        "visto_en": coincidencia.visto_en.isoformat(timespec="seconds") if coincidencia.visto_en else None,
        "revisada": coincidencia.revisada,
        "publicacion": serializar_publicacion(coincidencia.publicacion),
    }


def serializar_novedad(evento: EventoNovedades, novedad: Novedad) -> dict:
    return {
        "momento": evento.momento.isoformat(),
        "radicado": evento.radicado,
        "alias": evento.alias,
        "despacho": novedad.despacho or evento.despacho,
        "es_auto": novedad.es_auto,
        "coincidencias": list(novedad.coincidencias),
        "linea_base": evento.es_linea_base,
        "actuacion": serializar_actuacion(novedad.actuacion),
        "documentos": [serializar_documento(d) for d in novedad.documentos],
        "enlace_portal": URL_PORTAL_CONSULTA,
    }


def serializar_evento_publicacion(evento: EventoNovedades, coincidencia: CoincidenciaPublicacion) -> dict:
    return {
        "momento": evento.momento.isoformat(),
        "radicado": evento.radicado,
        "alias": evento.alias,
        "tipo_evento": "publicacion",
        **serializar_coincidencia(coincidencia),
    }


def serializar_consulta(consulta: ConsultaProceso) -> dict:
    return {
        "radicado": consulta.radicado,
        "enlace_portal": URL_PORTAL_CONSULTA,
        "procesos": [
            {
                "id_proceso": p.id_proceso,
                "despacho": p.despacho,
                "departamento": p.departamento,
                "sujetos": p.sujetos,
                "fecha_proceso": p.fecha_proceso.isoformat() if p.fecha_proceso else None,
                "fecha_ultima_actuacion": p.fecha_ultima_actuacion.isoformat() if p.fecha_ultima_actuacion else None,
                "es_privado": p.es_privado,
            }
            for p in consulta.procesos
        ],
        "detalles": [
            {
                "id_proceso": d.id_proceso,
                "ponente": d.ponente,
                "tipo_proceso": d.tipo_proceso,
                "clase_proceso": d.clase_proceso,
                "subclase_proceso": d.subclase_proceso,
                "recurso": d.recurso,
                "ubicacion": d.ubicacion,
                "codigo_despacho": d.codigo_despacho,
                "ultima_actualizacion": d.ultima_actualizacion.isoformat() if d.ultima_actualizacion else None,
            }
            for d in consulta.detalles
        ],
        "actuaciones": [
            {
                **serializar_actuacion(n.actuacion),
                "es_auto": n.es_auto,
                "coincidencias": list(n.coincidencias),
                "despacho": n.despacho,
            }
            for n in consulta.novedades
        ],
        "resumen": {"total_actuaciones": len(consulta.actuaciones), "total_autos": len(consulta.autos)},
    }


def novedades_a_mostrar(evento: EventoNovedades, solo_autos: bool) -> list[Novedad]:
    return evento.autos if solo_autos else evento.novedades


def evento_tiene_contenido(evento: EventoNovedades, solo_autos: bool) -> bool:
    """Las publicaciones que mencionan el radicado siempre se muestran, aunque se pidan solo autos."""
    return bool(novedades_a_mostrar(evento, solo_autos)) or bool(evento.publicaciones)


def linea_novedad(novedad: Novedad, con_documentos: bool = True) -> str:
    marca = "[AUTO]" if novedad.es_auto else "[    ]"
    fecha = novedad.actuacion.fecha_actuacion.isoformat() if novedad.actuacion.fecha_actuacion else "sin fecha"
    documentos = " (con documentos)" if novedad.actuacion.con_documentos else ""
    linea = f"  {marca} {fecha} #{novedad.actuacion.consecutivo:<4} {novedad.actuacion.texto}{documentos}"
    if con_documentos and novedad.documentos:
        detalles = [
            f"         Documento: {d.nombre or 'sin nombre'} -> {url_descarga_documento(d.id_documento)}"
            for d in novedad.documentos
        ]
        linea = "\n".join([linea, *detalles])
    return linea


def linea_publicacion(coincidencia: CoincidenciaPublicacion) -> str:
    p = coincidencia.publicacion
    fecha = p.fecha_publicacion.isoformat() if p.fecha_publicacion else "sin fecha"
    lineas = [
        f"  [PUBLICACION] {fecha} {p.tipo or 'Publicación'}: {p.titulo} ({coincidencia.donde}) -> {p.url_detalle}",
    ]
    if coincidencia.fragmento:
        lineas.append(f"         Fragmento: ...{coincidencia.fragmento}...")
    lineas.extend(f"         Documento: {d.etiqueta or 'archivo'} -> {d.url}" for d in p.documentos)
    return "\n".join(lineas)


def asunto_evento(evento: EventoNovedades) -> str:
    etiqueta = f"{evento.radicado}" + (f" ({evento.alias})" if evento.alias else "")
    autos = len(evento.autos)
    if autos:
        return f"[Consultor de Procesos] {autos} auto(s) nuevo(s) en {etiqueta}"
    if evento.publicaciones and not evento.novedades:
        return f"[Consultor de Procesos] {len(evento.publicaciones)} publicación(es) del despacho mencionan {etiqueta}"
    return f"[Consultor de Procesos] {len(evento.novedades)} actuacion(es) nueva(s) en {etiqueta}"


def formatear_evento(evento: EventoNovedades, solo_autos: bool = False) -> str:
    novedades = novedades_a_mostrar(evento, solo_autos)
    if not novedades and not evento.publicaciones:
        return ""
    etiqueta = evento.radicado + (f" ({evento.alias})" if evento.alias else "")
    resumen = f"  {len(evento.novedades)} actuacion(es) nueva(s), {len(evento.autos)} auto(s)"
    if evento.publicaciones:
        resumen += f", {len(evento.publicaciones)} publicacion(es) del despacho"
    lineas = [
        f"{evento.momento:%Y-%m-%d %H:%M} | Radicado {etiqueta}",
        f"  Despacho: {evento.despacho or 'desconocido'}",
        resumen,
    ]
    if evento.es_linea_base:
        lineas.append("  (linea base: actuaciones que ya existian al iniciar la vigilancia)")
    lineas.extend(linea_novedad(n) for n in novedades)
    lineas.extend(linea_publicacion(c) for c in evento.publicaciones)
    lineas.append(f"  Portal: {URL_PORTAL_CONSULTA}")
    return "\n".join(lineas)
