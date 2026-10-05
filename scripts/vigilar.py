"""Revisa desde fuera que un Consultor de Procesos desplegado esté sano. Solo biblioteca estándar.

Lo usan la CI (prueba de humo del contenedor), el CD (tras cada despliegue) y el monitor programado.
También sirve a mano:

    python scripts/vigilar.py https://consultor.mifirma.com --usuario monitor --clave "..."
    python scripts/vigilar.py http://127.0.0.1:8770 --sin-https --usuario ana --clave "..."

Comprueba que responda `/api/salud` (y, con --commit, que corra esa versión), que exija usuario y
contraseña, la protección CSRF, las cabeceras de seguridad, la redirección a HTTPS y, con
credenciales, el resumen de `/api/monitor`: vigilancia activa, verificaciones al día y sin errores.
Nunca modifica nada ni dispara consultas a la Rama Judicial. Termina con código 1 si algo falla.
Las credenciales también se leen de CONSULTOR_MONITOR_USUARIO y CONSULTOR_MONITOR_CLAVE.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TIEMPO_ESPERA = 15


@dataclass
class Respuesta:
    estado: int
    cabeceras: dict[str, str]
    texto: str

    def json(self) -> dict:
        try:
            datos = json.loads(self.texto)
        except ValueError:
            return {}
        return datos if isinstance(datos, dict) else {}


@dataclass
class Informe:
    fallos: list[str] = field(default_factory=list)
    lineas: list[str] = field(default_factory=list)

    def bien(self, texto: str) -> None:
        self.lineas.append(f"- ✅ {texto}")

    def mal(self, texto: str) -> None:
        self.fallos.append(texto)
        self.lineas.append(f"- ❌ {texto}")

    def aviso(self, texto: str) -> None:
        self.lineas.append(f"- ⚠️ {texto}")

    def comprobar(self, condicion: bool, texto: str, detalle: str = "") -> bool:
        if condicion:
            self.bien(texto)
        else:
            self.mal(f"{texto}{': ' + detalle if detalle else ''}")
        return condicion


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - firma de urllib
        return None


_ABRIDOR = urllib.request.build_opener(_SinRedirecciones)


def pedir(metodo: str, url: str, cabeceras: dict[str, str] | None = None, cuerpo: bytes | None = None) -> Respuesta:
    solicitud = urllib.request.Request(url, data=cuerpo, method=metodo, headers=cabeceras or {})
    try:
        with _ABRIDOR.open(solicitud, timeout=TIEMPO_ESPERA) as r:
            return Respuesta(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return Respuesta(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace"))


def credenciales(usuario: str, clave: str) -> dict[str, str]:
    token = base64.b64encode(f"{usuario}:{clave}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def revisar(args: argparse.Namespace) -> Informe:
    informe = Informe()
    base = args.url.rstrip("/")

    # 1. Salud (y versión esperada, esperando a que el contenedor nuevo arranque).
    limite = time.monotonic() + max(0, args.esperar)
    salud: Respuesta | None = None
    error = ""
    while True:
        try:
            salud = pedir("GET", f"{base}/api/salud")
            error = ""
        except OSError as exc:
            salud, error = None, str(exc)
        listo = salud is not None and salud.estado == 200 and salud.json().get("estado") == "ok"
        if listo and args.commit:
            listo = salud.json().get("commit") == args.commit
        if listo or time.monotonic() >= limite:
            break
        time.sleep(3)
    if salud is None:
        informe.mal(f"El servidor no responde en {base}: {error}")
        return informe
    datos_salud = salud.json()
    informe.comprobar(
        salud.estado == 200 and datos_salud.get("estado") == "ok",
        f"Responde /api/salud (versión {datos_salud.get('version')}, commit {str(datos_salud.get('commit'))[:12]})",
        f"HTTP {salud.estado} {salud.texto[:120]}",
    )
    if args.commit:
        informe.comprobar(
            datos_salud.get("commit") == args.commit,
            f"Corre el commit desplegado {args.commit[:12]}",
            f"responde {datos_salud.get('commit')}",
        )

    # 2. HTTPS y redirección desde http://.
    partes = urllib.parse.urlsplit(base)
    if partes.scheme == "https":
        try:
            plano = pedir("GET", urllib.parse.urlunsplit(("http", partes.netloc, "/", "", "")))
            informe.comprobar(
                plano.estado in (301, 302, 307, 308) and plano.cabeceras.get("location", "").startswith("https://"),
                "http:// redirige a https://",
                f"HTTP {plano.estado}",
            )
        except OSError as exc:
            informe.mal(f"http:// no responde: {exc}")
        informe.comprobar("strict-transport-security" in salud.cabeceras, "Cabecera HSTS")
    elif not args.sin_https:
        informe.mal("La dirección no es https:// (use --sin-https solo para pruebas locales)")

    # 3. Acceso protegido y CSRF.
    anonimo = pedir("GET", f"{base}/api/estado")
    informe.comprobar(anonimo.estado == 401, "La API exige usuario y contraseña", f"sin credenciales respondió HTTP {anonimo.estado}")
    if not (args.usuario and args.clave):
        informe.aviso("Sin credenciales (--usuario/--clave): no se revisó /api/monitor.")
        return informe
    auth = credenciales(args.usuario, args.clave)
    pagina = pedir("GET", f"{base}/", auth)
    if informe.comprobar(pagina.estado == 200 and "Consultor de Procesos" in pagina.texto, "La interfaz carga con las credenciales", f"HTTP {pagina.estado}"):
        informe.comprobar(pagina.cabeceras.get("x-content-type-options") == "nosniff", "Cabecera X-Content-Type-Options")
        informe.comprobar("frame-ancestors" in pagina.cabeceras.get("content-security-policy", ""), "Política de seguridad de contenido (CSP)")
    # Sin la cabecera CSRF la petición se rechaza antes de ejecutarse: no dispara ninguna verificación.
    csrf = pedir("POST", f"{base}/api/verificar", {**auth, "Content-Type": "application/json"}, b"{}")
    informe.comprobar(csrf.estado == 403, "Protección CSRF activa", f"POST sin cabecera respondió HTTP {csrf.estado}")

    # 4. Estado de la vigilancia.
    monitor = pedir("GET", f"{base}/api/monitor", auth)
    if not informe.comprobar(monitor.estado == 200, "Responde /api/monitor", f"HTTP {monitor.estado}"):
        return informe
    datos = monitor.json()
    vigilancia = datos.get("vigilancia") or {}
    for problema in datos.get("problemas", []):
        informe.mal(problema)
    for aviso in datos.get("avisos", []):
        informe.aviso(aviso)
    if datos.get("ok"):
        informe.bien(
            f"Vigilancia activa: {datos.get('procesos')} proceso(s), última verificación {datos.get('ultima_verificacion') or 'ninguna aún'}, "
            f"próxima {vigilancia.get('proxima_ejecucion') or '-'}"
        )
    presupuesto = datos.get("presupuesto") or {}
    if presupuesto:
        informe.bien(f"Solicitudes hoy a la Rama Judicial: {presupuesto.get('usado')} de {presupuesto.get('maximo')}")
    pendientes = datos.get("pendientes") or {}
    if pendientes:
        informe.bien(f"Pendientes por revisar: {pendientes.get('total')} actuación(es), {pendientes.get('autos')} auto(s)")
    return informe


def main(argv: list[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(description="Revisa que un Consultor de Procesos desplegado esté sano.")
    analizador.add_argument("url", help="Dirección pública, por ejemplo https://consultor.mifirma.com")
    analizador.add_argument("--usuario", default=os.environ.get("CONSULTOR_MONITOR_USUARIO", ""))
    analizador.add_argument("--clave", default=os.environ.get("CONSULTOR_MONITOR_CLAVE", ""))
    analizador.add_argument("--commit", help="Exigir que corra este commit (tras un despliegue).")
    analizador.add_argument("--esperar", type=float, default=0, help="Segundos a esperar a que responda /api/salud (y el commit).")
    analizador.add_argument("--sin-https", action="store_true", help="Permitir http:// (pruebas locales).")
    analizador.add_argument("--informe", help="Escribir el informe en Markdown en este archivo.")
    args = analizador.parse_args(argv)

    informe = revisar(args)
    encabezado = "Todo en orden" if not informe.fallos else f"{len(informe.fallos)} problema(s) por atender"
    texto = f"### Consultor de Procesos: {encabezado}\n\n{args.url}\n\n" + "\n".join(informe.lineas) + "\n"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(texto)
    for destino in filter(None, [args.informe, os.environ.get("GITHUB_STEP_SUMMARY")]):
        with open(destino, "a", encoding="utf-8") as archivo:
            archivo.write(texto + "\n")
    return 1 if informe.fallos else 0


if __name__ == "__main__":
    sys.exit(main())
