"""Página visor de un documento: el PDF del auto a pantalla completa, con enlaces a la fuente oficial.

Se sirve en `/documento/{idRegActuacion}[/{idRegDocumento}]`. Abre en una pestaña nueva desde la
interfaz, muestra el texto completo de la actuación y el documento seleccionado, y ofrece la
descarga directa desde la Rama Judicial y el portal de consulta.
"""

from __future__ import annotations

import json
from html import escape

from ...dominio.modelos import Documento, Novedad
from ...enlaces import url_descarga_documento

_ESTILOS = """
  :root { --primario: #1f4e79; --linea: #e1e5eb; --tinta: #1c2430; --suave: #5b6675; --ambar: #b7791f; --ambar-suave: #fdf3dd;
          --mono: ui-monospace, "Cascadia Mono", Consolas, monospace; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { display: flex; flex-direction: column; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--tinta); background: #f4f5f7; }
  header { background: #fff; border-bottom: 1px solid var(--linea); padding: 10px 16px; display: grid; gap: 8px; }
  .fila { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; justify-content: space-between; }
  .titulo { font-weight: 650; font-size: 1.02rem; }
  .meta { color: var(--suave); font-size: .82rem; }
  .radicado { font-family: var(--mono); }
  .insignia { display: inline-block; font-size: .7rem; font-weight: 700; text-transform: uppercase; padding: 2px 8px; border-radius: 6px;
              background: var(--ambar-suave); color: var(--ambar); margin-right: 6px; vertical-align: middle; }
  .btn { display: inline-block; padding: 5px 11px; border-radius: 8px; border: 1px solid var(--linea); background: #fff; color: var(--tinta);
         text-decoration: none; font: inherit; font-size: .84rem; cursor: pointer; }
  .btn:hover { border-color: #c5ccd6; }
  .btn.primario { background: var(--primario); border-color: var(--primario); color: #fff; }
  .btn.activo { background: #e8f0f8; border-color: var(--primario); color: var(--primario); }
  .acciones { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .texto { white-space: pre-wrap; font-size: .88rem; max-height: 7em; overflow: auto; background: #f7f8fa; border-radius: 8px; padding: 6px 10px; }
  .aviso { background: var(--ambar-suave); color: #7a4f0e; padding: 6px 10px; border-radius: 8px; font-size: .85rem; }
  main { flex: 1; min-height: 0; display: flex; }
  iframe { flex: 1; border: 0; background: #525659; }
  .vacio { margin: auto; text-align: center; color: var(--suave); padding: 40px; max-width: 560px; }
"""


def _fecha(valor) -> str:
    return valor.isoformat() if valor else "sin fecha"


def _cabecera_html(titulo: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"es\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{escape(titulo)} · Consultor de Procesos</title>\n<style>{_ESTILOS}</style>\n</head>\n<body>\n"
    )


def renderizar_visor(
    novedad: Novedad,
    documentos: list[Documento],
    seleccionado: Documento | None,
    alias: str | None = None,
    titulo_proceso: str | None = None,
    aviso: str | None = None,
    portal: str = "",
) -> str:
    a = novedad.actuacion
    radicado = novedad.radicado or a.radicado
    etiqueta = titulo_proceso or alias or f"Proceso {radicado}"
    partes = [_cabecera_html(a.actuacion or "Documento")]
    partes.append("<header>\n<div class=\"fila\">\n<div class=\"acciones\">")
    partes.append(f"<a class=\"btn\" href=\"/#/proceso/{escape(radicado)}\">&larr; Volver al proceso</a>")
    partes.append("</div>\n<div class=\"acciones\">")
    if seleccionado is not None:
        partes.append(
            f"<a class=\"btn\" href=\"/api/documentos/{seleccionado.id_documento}?descargar=1\">Descargar</a>"
            f"<a class=\"btn\" href=\"{escape(url_descarga_documento(seleccionado.id_documento))}\" target=\"_blank\" rel=\"noopener\""
            " title=\"Descarga directa desde el servidor de la Rama Judicial\">Abrir en la Rama Judicial</a>"
        )
    partes.append(
        f"<button class=\"btn\" type=\"button\" id=\"btn-portal\" data-radicado=\"{escape(radicado)}\""
        " title=\"Copia el radicado y abre el portal de consulta de procesos\">Portal de consulta</button>"
    )
    partes.append("</div>\n</div>")
    insignia = "<span class=\"insignia\">Auto</span>" if novedad.es_auto else ""
    partes.append(f"<div>\n<div class=\"titulo\">{insignia}{escape(a.actuacion or 'Actuación')}</div>")
    meta = [escape(etiqueta), f"<span class=\"radicado\">{escape(radicado)}</span>"]
    if novedad.despacho:
        meta.append(escape(novedad.despacho))
    meta.append(f"Actuación del {_fecha(a.fecha_actuacion)} · #{a.consecutivo}")
    partes.append(f"<div class=\"meta\">{' · '.join(meta)}</div>\n</div>")
    if a.anotacion:
        partes.append(f"<div class=\"texto\">{escape(a.anotacion)}</div>")
    if aviso:
        partes.append(f"<div class=\"aviso\">{escape(aviso)}</div>")
    if len(documentos) > 1:
        enlaces = []
        for d in documentos:
            clase = "btn activo" if seleccionado is not None and d.id_documento == seleccionado.id_documento else "btn"
            nombre = d.nombre or f"Documento {d.id_documento}"
            enlaces.append(f"<a class=\"{clase}\" href=\"/documento/{a.id_registro}/{d.id_documento}\">{escape(nombre)}</a>")
        partes.append("<div class=\"acciones\"><span class=\"meta\">Documentos:</span>" + "".join(enlaces) + "</div>")
    elif seleccionado is not None:
        partes.append(f"<div class=\"meta\">Documento: {escape(seleccionado.nombre or f'Documento {seleccionado.id_documento}')}</div>")
    partes.append("</header>\n<main>")
    if seleccionado is not None:
        partes.append(
            f"<iframe src=\"/api/documentos/{seleccionado.id_documento}\" title=\"{escape(seleccionado.nombre or 'Documento')}\"></iframe>"
        )
    else:
        mensaje = (
            "Esta actuación no tiene documentos publicados en el portal. El texto completo de la actuación está arriba."
            if not a.con_documentos
            else "El portal indica que hay documentos, pero todavía no entrega la lista. Vuelva a intentarlo en unos minutos."
        )
        partes.append(
            f"<div class=\"vacio\">{mensaje}<br><small>Puede revisar el expediente en el portal de la Rama Judicial con el botón"
            " «Portal de consulta», que copia el radicado.</small></div>"
        )
    partes.append("</main>")
    partes.append(
        "<script>\ndocument.getElementById(\"btn-portal\").addEventListener(\"click\", async (ev) => {\n"
        "  const radicado = ev.currentTarget.dataset.radicado;\n"
        "  try { await navigator.clipboard.writeText(radicado); } catch (e) { /* sin portapapeles */ }\n"
        f"  window.open({json.dumps(portal)}, \"_blank\", \"noopener\");\n"
        "});\n</script>\n</body>\n</html>\n"
    )
    return "\n".join(partes)


def renderizar_error(mensaje: str, titulo: str = "No encontrado") -> str:
    return (
        _cabecera_html(titulo)
        + f"<main><div class=\"vacio\"><strong>{escape(titulo)}</strong><br>{escape(mensaje)}<br><br>"
        "<a class=\"btn\" href=\"/\">Volver a la interfaz</a></div></main>\n</body>\n</html>\n"
    )
