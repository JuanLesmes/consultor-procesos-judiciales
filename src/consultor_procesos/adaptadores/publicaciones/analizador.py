"""Traducción del HTML del portal Publicaciones Procesales a modelos del dominio.

El portal es Liferay con un portlet propio. La lista filtrada llega como HTML servido en el
servidor: cada publicación es una fila con un título enlazado al detalle (el parámetro
`articleId` de ese enlace es el identificador estable), un bloque de categorías
("Tipo de publicación:", "Despacho:110013103001 - JUZGADO ..."), la fecha de publicación y
un resumen que el portal deja dentro de un comentario HTML pero que trae los enlaces a los
PDF (por ejemplo "ESTADO -> VER" y "AUTOS -> VER").
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

from ...dominio.errores import RespuestaInesperada
from ...dominio.modelos import DocumentoPublicado, PaginaPublicaciones, Publicacion
from ...enlaces import URL_PUBLICACIONES

# Cada publicación es una fila `<tr class=" col-xs-12 tramites ">`; el resumen puede traer tablas
# anidadas, así que no sirve cortar en `</tbody>`. Si el portal cambiara la clase de la fila,
# se recurre a partir por el título de cada publicación.
_RE_FILAS = re.compile(
    r'<tr class="[^"]*tramites[^"]*"[^>]*>(?P<cuerpo>.*?)(?=<tr class="[^"]*tramites|\Z)', re.S
)
_RE_BLOQUES = re.compile(
    r'<div class="titulo-publicacion[^"]*">(?P<cuerpo>.*?)(?=<div class="titulo-publicacion|\Z)', re.S
)
_RE_ENLACE = re.compile(r'<a\s[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<texto>.*?)</a>', re.S)
_RE_CATEGORIA = re.compile(r'<span class="categoria-ep">(.*?)</span>', re.S)
_RE_FECHA = re.compile(r"Fecha de Publicaci[^:<]{1,12}:\s*</i>\s*(\d{4}-\d{2}-\d{2})")
_RE_RESUMEN = re.compile(r'class="resume-ep">(?P<cuerpo>.*?)</p>\s*</div>', re.S)
_RE_CELDA = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_RE_TOTAL = re.compile(r"Mostrando (?:el intervalo \d[\d.]* - \d[\d.]* de )?([\d.]+) resultados")
_RE_DESPACHO = re.compile(r"(\d{12})\s*-\s*(.*)")
_RE_ETIQUETAS = re.compile(r"<[^>]+>")
_RE_COMENTARIOS = re.compile(r"<!--.*?-->", re.S)
_RE_ESPACIOS = re.compile(r"[\s ]+")
_TEXTOS_GENERICOS = {"VER", "VER DOCUMENTO", "DESCARGAR", "AQUI", "AQUÍ", "CLIC AQUI", "CLIC AQUÍ", "ENLACE", "LINK"}


def texto_plano(fragmento: str) -> str:
    """Quita etiquetas y comentarios HTML, decodifica entidades y colapsa espacios."""
    sin_comentarios = _RE_COMENTARIOS.sub(" ", fragmento)
    sin_etiquetas = _RE_ETIQUETAS.sub(" ", sin_comentarios)
    return _RE_ESPACIOS.sub(" ", html.unescape(sin_etiquetas)).strip()


def es_enlace_documento(href: str) -> bool:
    bajo = href.lower()
    return "/documents/" in bajo or "document_library" in bajo or bajo.split("?")[0].endswith((".pdf", ".doc", ".docx", ".xlsx"))


def _id_desde_enlace(href: str) -> str | None:
    consulta = parse_qs(urlparse(html.unescape(href)).query)
    for clave, valores in consulta.items():
        if clave.endswith("articleId") and valores and valores[0].strip():
            return valores[0].strip()
    return None


def _documentos(cuerpo: str, url_base: str) -> tuple[DocumentoPublicado, ...]:
    documentos: list[DocumentoPublicado] = []
    vistos: set[str] = set()

    def agregar(href: str, etiqueta: str) -> None:
        url = urljoin(url_base, html.unescape(href))
        if url in vistos:
            return
        vistos.add(url)
        documentos.append(DocumentoPublicado(etiqueta=etiqueta or _nombre_archivo(url), url=url))

    # Primero las celdas de tabla: "ESTADO ... <a>VER</a>" -> la etiqueta es el texto de la celda.
    for celda in _RE_CELDA.findall(cuerpo):
        enlaces = [(h, t) for h, t in _RE_ENLACE.findall(celda) if es_enlace_documento(h)]
        if not enlaces:
            continue
        texto_celda = texto_plano(_RE_ENLACE.sub(" ", celda))
        for href, texto in enlaces:
            etiqueta = texto_plano(texto)
            if not etiqueta or etiqueta.upper() in _TEXTOS_GENERICOS:
                etiqueta = texto_celda
            agregar(href, etiqueta)
    # Luego cualquier otro enlace a documento del bloque.
    for href, texto in _RE_ENLACE.findall(cuerpo):
        if es_enlace_documento(href):
            etiqueta = texto_plano(texto)
            agregar(href, "" if etiqueta.upper() in _TEXTOS_GENERICOS else etiqueta)
    return tuple(documentos)


def _nombre_archivo(url: str) -> str:
    ruta = urlparse(url).path
    partes = [p for p in ruta.split("/") if p]
    # Liferay: /documents/<grupo>/<carpeta>/<nombre>/<uuid>
    for parte in reversed(partes):
        if "." in parte and not re.fullmatch(r"[0-9a-f-]{20,}", parte):
            return html.unescape(parte.replace("+", " "))
    return partes[-1] if partes else url


def analizar_bloque(cuerpo: str, url_base: str, id_estructura: int | None) -> Publicacion | None:
    enlaces = _RE_ENLACE.findall(cuerpo)
    if not enlaces:
        return None
    href_detalle, texto_titulo = enlaces[0]
    titulo = texto_plano(texto_titulo)
    url_detalle = urljoin(url_base, html.unescape(href_detalle))

    categorias: dict[str, str] = {}
    for crudo in _RE_CATEGORIA.findall(cuerpo):
        texto = texto_plano(crudo)
        if ":" in texto:
            clave, valor = texto.split(":", 1)
            categorias[texto_plano(clave).lower()] = valor.strip()

    despacho_codigo, despacho = "", categorias.get("despacho", "")
    coincidencia = _RE_DESPACHO.match(despacho)
    if coincidencia:
        despacho_codigo, despacho = coincidencia.group(1), coincidencia.group(2).strip()

    fecha_match = _RE_FECHA.search(cuerpo)
    fecha = date.fromisoformat(fecha_match.group(1)) if fecha_match else None

    resumen_match = _RE_RESUMEN.search(cuerpo)
    resumen = texto_plano(resumen_match.group("cuerpo")) if resumen_match else ""

    identificador = _id_desde_enlace(href_detalle)
    if not identificador:
        base = f"{despacho_codigo}|{titulo}|{fecha.isoformat() if fecha else ''}|{url_detalle}"
        identificador = "h" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    return Publicacion(
        id_publicacion=identificador,
        tipo=categorias.get("tipo de publicación", categorias.get("tipo de publicacion", "")),
        despacho_codigo=despacho_codigo,
        despacho=despacho,
        titulo=titulo,
        url_detalle=url_detalle,
        id_estructura=id_estructura,
        fecha_publicacion=fecha,
        resumen=resumen,
        documentos=_documentos(cuerpo, url_base),
        departamento=categorias.get("departamento", ""),
        municipio=categorias.get("municipio", ""),
        entidad=categorias.get("entidad", ""),
        especialidad=categorias.get("especialidad", ""),
    )


def analizar_total(pagina_html: str) -> int | None:
    coincidencia = _RE_TOTAL.search(pagina_html)
    if not coincidencia:
        return None
    return int(coincidencia.group(1).replace(".", ""))


def analizar_lista(
    pagina_html: str,
    id_estructura: int | None = None,
    pagina: int = 1,
    por_pagina: int = 75,
    url_base: str = URL_PUBLICACIONES,
) -> PaginaPublicaciones:
    publicaciones: list[Publicacion] = []
    bloques = [f.group("cuerpo") for f in _RE_FILAS.finditer(pagina_html)]
    bloques = [b for b in bloques if "titulo-publicacion" in b] or [b.group("cuerpo") for b in _RE_BLOQUES.finditer(pagina_html)]
    for cuerpo in bloques:
        publicacion = analizar_bloque(cuerpo, url_base, id_estructura)
        if publicacion is not None:
            publicaciones.append(publicacion)
    total = analizar_total(pagina_html)
    if total is not None and not publicaciones and total > por_pagina * (pagina - 1):
        # El portal anuncia resultados para esta página pero no se reconoció ninguna fila:
        # cambió el HTML. Seguir como si no hubiera publicaciones escondería los estados.
        raise RespuestaInesperada(
            f"El portal de publicaciones informa {total} resultado(s) pero no se reconoció ninguna publicación; "
            "probablemente cambió la estructura de la página."
        )
    if total is None:
        total = len(publicaciones) + (por_pagina * (pagina - 1))
    return PaginaPublicaciones(publicaciones=tuple(publicaciones), pagina=pagina, por_pagina=por_pagina, total=total)
