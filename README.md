# Consultor de Procesos

[![CI](https://github.com/JuanLesmes/consultor-procesos-judiciales/actions/workflows/ci.yml/badge.svg)](https://github.com/JuanLesmes/consultor-procesos-judiciales/actions/workflows/ci.yml)

Vigilancia **cortés** de procesos judiciales en la Rama Judicial de Colombia, pensada para correr
en un servidor (VPS) y usarse desde el navegador.

Registra números de radicación (23 dígitos), los verifica a horas fijas contra la **Consulta de
Procesos Nacional Unificada (CPNU)**, detecta **autos** y demás actuaciones nuevas, enlaza el
**PDF del auto** cuando el despacho lo publicó, revisa los **estados, avisos y traslados** que cada
despacho publica en su micrositio (portal Publicaciones Procesales) buscando sus radicados incluso
dentro de los PDF, y avisa por la interfaz web, correo electrónico, webhook o un archivo JSON Lines.
Todo el diseño gira en torno a una idea: consultar lo mínimo indispensable y comportarse como un
cliente identificable y respetuoso, que es la única forma sostenible de no tumbar el portal y de
no ser bloqueado.

| Documento | Contenido |
| --- | --- |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Paso a paso para dejarlo en un VPS con HTTPS, despliegue automático y monitor |
| [docs/GUIA_DE_PRUEBAS.md](docs/GUIA_DE_PRUEBAS.md) | Qué hace, cómo probarlo y casos de prueba con su resultado esperado |
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Diseño: capas, flujo de una verificación, cortesía, seguridad, datos |
| [docs/FUENTES.md](docs/FUENTES.md) | Sitios oficiales de consulta y qué se puede automatizar en cada uno |

## Qué hace y qué no hace

| Sí | No |
| --- | --- |
| Usa la misma API JSON pública que consume el portal `consultaprocesos.ramajudicial.gov.co` y las páginas públicas de Publicaciones Procesales | No simula clics ni sesiones de navegador |
| Se identifica con un `User-Agent` propio y un correo de contacto | No se hace pasar por un navegador |
| Limita el ritmo (12 solicitudes/min por defecto), pausa entre radicados y reintenta con retroceso exponencial respetando `Retry-After` | No rota direcciones IP ni usa proxies |
| Se detiene solo (cortacircuito) cuando la fuente falla y respeta un presupuesto diario de solicitudes | No resuelve ni evade CAPTCHAs ni bloqueos |
| Recuerda lo ya visto para pedir solo lo nuevo, y guarda los PDF ya descargados | No descarga documentos ni datos que no necesita |
| Avisa en voz alta si la Rama Judicial cambia el formato de sus respuestas | No sigue funcionando "a ciegas" con datos que ya no entiende |

La API de la CPNU no tiene CAPTCHA, así que en este flujo no hay nada que evadir. Si el portal
llegara a responder con 429 o a bloquear, la respuesta correcta es **bajar el ritmo**
(configuración) o **escribir a la Rama Judicial**. Los sistemas TYBA (Justicia XXI Web) y SAMAI
sí exigen CAPTCHA y por eso no están soportados.

## Cómo se ejecuta

En producción corre en Docker detrás de **Caddy**, que pone el HTTPS con certificado automático.
Cada cambio que llega a `main` pasa por la CI (pruebas e imagen), se publica en GHCR, se instala
en el VPS con respaldo previo y vuelta atrás automática, y un monitor programado revisa después
de cada verificación que siga vigilando bien. El detalle está en [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).

```text
 GitHub ── push a main ──> CI (pruebas + imagen + prueba de humo)
                             └─> CD: imagen a GHCR ─SSH─> VPS: respaldo, actualización, /api/salud
                                                                      │
 navegador ──HTTPS──> Caddy ──> consultor (contenedor) ──> Rama Judicial (CPNU y Publicaciones)
                                     │
 Monitor (GitHub, 4 veces al día) ───┘  /api/monitor: ¿vigilando, al día, sin errores? -> issue si no
```

### Probarlo en su computador con Docker (igual que en el servidor)

1. Cree el archivo `.env` a partir del ejemplo y genere un usuario:

```bash
cp .env.ejemplo .env
```

```bash
docker compose run --rm consultor consultor-procesos crear-usuario ana
```

2. Pegue la línea que imprime en `CONSULTOR_USUARIOS=` del `.env`, ponga su correo en
   `CONSULTOR_CONTACTO=` y deje `COMPOSE_PROFILES=` vacío (en local no hace falta Caddy).
3. Arranque y entre a `http://127.0.0.1:8770` con ese usuario:

```bash
docker compose up --build
```

La base de datos, los PDF descargados y los respaldos quedan en la carpeta `datos/`.

### Desarrollo sin Docker

Requiere Python 3.11 o superior. Dependencias: `httpx` y `pypdf`; para pruebas, `pytest` y `respx`.
La interfaz web usa solo la biblioteca estándar.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -e ".[dev]"
```

```bash
.venv\Scripts\consultor-procesos web
```

Sin usuarios configurados el servidor solo acepta escuchar en `127.0.0.1`. Para escuchar en la red
exige `CONSULTOR_USUARIOS` (o `--sin-autenticacion`, solo dentro de una red interna).

## La interfaz

La interfaz tiene dos niveles:

1. **Procesos.** Una tarjeta por radicado con su título (el alias o los sujetos procesales),
   el número, el despacho, el tipo y la clase de proceso, la última actuación y cuántos autos
   quedan por revisar. Al agregar un radicado se lee su ficha y todo su historial (línea base)
   en segundo plano.
2. **Ficha del proceso.** Los datos del proceso (despacho, sujetos, ponente, fechas) y pestañas
   con el **historial completo**, solo los **autos**, los **estados y avisos** del despacho que
   lo mencionan y la bitácora de **verificaciones**. Cada auto con documento tiene el botón
   **Abrir auto**, que abre el PDF publicado por el despacho, con enlaces para descargarlo,
   abrirlo desde el servidor de la Rama Judicial y llegar al portal de consulta.

La pestaña **Novedades** reúne lo nuevo de todos los procesos. Desde la barra superior se puede
**verificar ahora**, **pausar o reanudar la vigilancia**, hacer una **consulta puntual** y seguir
el **presupuesto diario** de solicitudes. "Marcar revisada" es compartido por todos los usuarios.

**La vigilancia arranca con el servidor** y sigue el horario configurado: de lunes a viernes a las
07:00, 10:00, 13:00 y 17:00 (hora de Colombia), y nada después de las 18:00, porque los despachos
registran los autos en horario laboral. Si el servidor arranca cuando ya pasó una de esas horas,
verifica de inmediato.

El portal de la Rama Judicial es una aplicación de una sola página sin enlaces profundos, así que
no existe una URL que lleve a un auto concreto: **Abrir auto** muestra el PDF obtenido con la
misma API del portal, y el botón **Portal** copia el radicado y abre la página oficial.

## Configuración

Casi todo funciona con los valores por defecto. Lo que cambia de una instalación a otra va en
variables de entorno (archivo `.env` del despliegue, ver [.env.ejemplo](.env.ejemplo)):

| Variable | Para qué |
| --- | --- |
| `CONSULTOR_USUARIOS` | Usuarios de la interfaz (`usuario:hash`, separados por comas). Se generan con `consultor-procesos crear-usuario NOMBRE`; la contraseña no se guarda. |
| `CONSULTOR_CONTACTO` | Correo con el que el programa se identifica ante la Rama Judicial. Sin él, el monitor lo advierte. |
| `CONSULTOR_CONFIG` | Archivo TOML con ajustes finos (en el contenedor, `/datos/consultor_procesos.toml`). |
| `CONSULTOR_SMTP_CONTRASENA` | Contraseña del correo, si se activan los avisos por correo. |
| `DOMINIO`, `COMPOSE_PROFILES`, `TZ` | Despliegue: dominio para Caddy, si se levanta Caddy propio, zona horaria. |

Los ajustes finos (horario, palabras clave, ritmo, publicaciones, correo, webhook) se documentan en
[consultor_procesos.ejemplo.toml](consultor_procesos.ejemplo.toml).

## Uso desde la consola

Los mismos comandos funcionan en el servidor anteponiendo `docker compose exec consultor`:

```bash
consultor-procesos agregar 11001400300120240012345 --alias "Caso demo"
```

```bash
consultor-procesos verificar
```

```bash
consultor-procesos consultar 11001400300120240012345 --solo-autos
```

```bash
consultor-procesos historial
```

```bash
consultor-procesos respaldar --destino respaldos
```

Otros: `listar`, `quitar`, `autos`, `publicaciones --revisar`, `vigilar` (solo la vigilancia, sin
interfaz), `crear-usuario`, `iniciar-config` y `simular-novedad` (para pruebas: olvida localmente las
últimas actuaciones de un radicado para que la próxima verificación las traiga como nuevas). La primera verificación de un radicado registra la
**línea base** (lo que ya existía) sin notificar; desde la segunda solo se avisa lo nuevo.

## Cómo detecta los autos

Cada actuación tiene un *tipo* (`actuacion`) y una *anotación* (`anotacion`). Muchos despachos
registran el tipo genérico "Constancia secretarial" y describen el auto en la anotación ("AUTO
ORDENA REQUERIR..."), así que el detector revisa **ambos** campos: normaliza el texto (sin tildes,
mayúsculas) y busca la palabra completa `AUTO` o `AUTOS` ("AUTOMOTOR" o "AUTORIZA" no cuentan;
"AUTO-ADMISORIO" sí). Se amplía con `verificacion.palabras_clave`, por ejemplo
`["AUTO", "SENTENCIA", "FIJACION ESTADO"]`.

## Documentos y estados de los despachos

- Al detectar un **auto nuevo con documentos**, la verificación pide la lista (una solicitud) y la
  incluye en la notificación. Los PDF se guardan una sola vez en `datos/documentos/`.
- Cada despacho publica estados, avisos, traslados y autos masivos en **Publicaciones Procesales**.
  El programa revisa cada despacho a lo sumo una vez cada 20 horas, busca sus radicados en el título,
  el resumen, la página de detalle de cada publicación (muchos juzgados publican allí un PDF por auto,
  con el radicado corto en el nombre) y dentro de los PDF (radicado completo, 21 dígitos o
  año-consecutivo), y muestra cada coincidencia en la pestaña **Estados y avisos**.

## Cómo evita sobrecargar el portal

| Mecanismo | Valor por defecto | Para qué |
| --- | --- | --- |
| Identificación honesta | `User-Agent` propio con contacto | Que un administrador pueda escribir antes de bloquear |
| Limitador de tasa (cubeta de fichas) | 12 solicitudes/min, ráfaga 1 | Ritmo sostenido de una solicitud cada 5 s |
| Pausa entre radicados | 3 s | Respiro adicional entre procesos |
| Reintentos con retroceso exponencial | 4 intentos, 2 s -> 4 s -> 8 s, tope 60 s, respeta `Retry-After` | Recuperarse de 429/5xx sin insistir en ráfaga |
| Cortacircuito | 5 fallos seguidos -> 5 min sin consultar; 401/403 cuentan como fallo | No seguir golpeando una fuente caída o que nos bloqueó |
| Presupuesto diario | 400 solicitudes, atómico aunque haya varios procesos | Tope duro aunque haya un error de configuración |
| Huella de cambios | se compara la fecha de última actuación | Una sola solicitud por radicado cuando nada cambió |
| Lectura incremental | hasta la primera actuación conocida (máximo 20 páginas) | Solo las páginas necesarias, sin dejar huecos |
| Una operación a la vez | candado único en el servidor | Que verificación, vigilancia y consultas no se solapen |
| Horario de verificación | lunes a viernes 07:00, 10:00, 13:00 y 17:00; nada después de las 18:00 | Los autos se registran en horario laboral |

Costo aproximado por radicado y verificación: **1 solicitud** si no hubo cambios, 2 a 4 si los hubo,
más una por cada auto nuevo con documentos. Con 50 radicados y 4 verificaciones diarias se consumen
unas 200 a 300 solicitudes.

## Estados de una verificación

| Estado | Significado |
| --- | --- |
| `OK` | Se revisaron actuaciones; puede haber novedades o no. |
| `SIN_CAMBIOS` | La fecha de última actuación no cambió; no se descargaron actuaciones (1 solicitud). |
| `NO_ENCONTRADO` | La fuente no conoce ese radicado. |
| `PRIVADO` | El proceso es privado: la fuente no publica actuaciones. |
| `ERROR` | Error definitivo para ese radicado, o la respuesta cambió de formato (campo renombrado); el lote continúa y el monitor lo reporta. |
| `OMITIDO` | La fuente no responde o se agotó el presupuesto; el resto del lote se pospone. |

## Pruebas

```bash
pytest
```

La suite (unas 350 pruebas) no toca la red: el cliente HTTP se prueba con respuestas simuladas, la
interfaz web sin puertos y con un servidor real en un puerto libre, y las esperas usan un reloj
inyectado. Hay una prueba contra la API real, desactivada por defecto, que hace exactamente tres
solicitudes y confirma que el analizador sigue reconociendo las respuestas oficiales:

```bash
CONSULTOR_PRUEBAS_EN_VIVO=1 CONSULTOR_RADICADO_PRUEBA=11001400300120240012345 pytest -m en_vivo
```

## Estructura

```text
src/consultor_procesos/
  dominio/          modelos, reglas (detector de autos, huella, novedades), puertos, errores
  aplicacion/       ServicioVigilancia, ServicioPublicaciones, Planificador y Horario
  adaptadores/
    cpnu/           cliente HTTP cortés y analizador estricto de la API de la CPNU
    publicaciones/  cliente y analizador HTML del portal Publicaciones Procesales
    persistencia/   repositorio SQLite (y uno en memoria para pruebas)
    notificacion/   consola, JSONL, correo, webhook, compuesto, formato compartido
    web/            servidor HTTP, seguridad (usuarios, CSRF, cabeceras) y la página de la interfaz
  infraestructura/  cortesía (limitador, reintentos, cortacircuito, presupuesto), solicitante HTTP, PDF, registro
  configuracion.py  TOML + variables de entorno
  cli.py            línea de comandos (incluye el servidor `web`)
tests/              pruebas unitarias, de integración simulada, de CLI y de la interfaz web
scripts/vigilar.py  revisión externa de un despliegue (CI, CD y monitor)
despliegue/         script que instala una versión en el VPS
.github/workflows/  ci.yml, cd.yml, monitor.yml
Dockerfile, docker-compose.yml, docker-compose.compartido.yml, Caddyfile, .env.ejemplo
```

## Consideraciones legales y de datos

- La información de la CPNU es pública, pero contiene nombres de las partes. La base de datos, el
  archivo `novedades.jsonl`, los respaldos y la carpeta `documentos/` pueden incluirlos: trátelos
  como información sensible conforme a la Ley 1581 de 2012. Nada de eso se sube al repositorio.
- La interfaz exige usuario y contraseña en cuanto escucha fuera de `127.0.0.1`, y debe servirse
  solo por HTTPS (Caddy). No tiene doble factor de autenticación.
- Use la herramienta para radicados de su interés legítimo (procesos propios o de sus clientes), no
  para barridos masivos.

## Licencia

MIT.
