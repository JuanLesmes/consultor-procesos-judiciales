# Consultor de Procesos

Vigilancia **cortés** de procesos judiciales en la Rama Judicial de Colombia.

Registra números de radicación (23 dígitos), los verifica periódicamente contra la
**Consulta de Procesos Nacional Unificada (CPNU)**, detecta **autos** y demás actuaciones
nuevas, enlaza el **PDF del auto** cuando el despacho lo publicó, revisa los **estados,
avisos y traslados** que cada despacho publica en su micrositio (portal Publicaciones
Procesales) buscando sus radicados incluso dentro de los PDF, y avisa por una interfaz web
local, consola, archivo JSON Lines, correo electrónico o webhook. Todo el diseño gira
en torno a una idea: consultar lo mínimo indispensable y comportarse como un cliente
identificable y respetuoso, que es la única forma sostenible de no tumbar el portal y de
no ser bloqueado.

El diseño detallado está en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) y el inventario de
los sitios oficiales de consulta, con lo que se puede y no se puede automatizar, en
[docs/FUENTES.md](docs/FUENTES.md).

## Qué hace y qué no hace

| Sí | No |
| --- | --- |
| Usa la misma API JSON pública que consume el portal `consultaprocesos.ramajudicial.gov.co` y las páginas públicas de Publicaciones Procesales | No simula clics ni sesiones de navegador |
| Se identifica con un `User-Agent` propio y un correo de contacto | No se hace pasar por un navegador |
| Limita el ritmo (12 solicitudes/min por defecto), pausa entre radicados y reintenta con retroceso exponencial respetando `Retry-After` | No rota direcciones IP ni usa proxies |
| Se detiene solo (cortacircuito) cuando la fuente falla y respeta un presupuesto diario de solicitudes | No resuelve ni evade CAPTCHAs ni bloqueos |
| Recuerda lo ya visto para pedir solo lo nuevo, y guarda en caché los PDF ya descargados | No descarga documentos ni datos que no necesita |

La API de la CPNU no tiene CAPTCHA, así que en este flujo no hay nada que evadir. Si el
portal llegara a responder con 429 o a bloquear, la respuesta correcta es **bajar el ritmo**
(configuración) o **escribir a la Rama Judicial**. Los sistemas TYBA (Justicia XXI Web) y
SAMAI sí exigen CAPTCHA y por eso no están soportados; la arquitectura permite añadir
adaptadores cuando exista una vía legítima.

## Requisitos

- Python 3.11 o superior (probado con 3.14 en Windows 11).
- Dependencias de ejecución: `httpx` (HTTP) y `pypdf` (lectura de los PDF de los estados). Para pruebas: `pytest` y `respx`. La interfaz web usa solo la biblioteca estándar.

## Instalación

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -e ".[dev]"
```

En Linux o macOS la activación es `source .venv/bin/activate`. El comando `consultor-procesos`
queda disponible dentro del entorno; también funciona `python -m consultor_procesos`.

## Interfaz web (recomendada)

```bash
consultor-procesos web
```

Abre `http://127.0.0.1:8765/` en el navegador (solo accesible desde su equipo). La interfaz
tiene dos niveles:

1. **Procesos.** Una tarjeta por radicado con su título (el alias o los sujetos procesales),
   el número, el despacho, el tipo y la clase de proceso, la última actuación y cuántos autos
   quedan por revisar. Al agregar un radicado se lee su ficha y todo su historial (línea base)
   en segundo plano.
2. **Ficha del proceso.** Al pulsar una tarjeta: los datos del proceso (despacho, sujetos,
   ponente, fechas) y pestañas con el **historial completo** de actuaciones, solo los
   **autos**, los **estados y avisos** del despacho que lo mencionan y la bitácora de
   **verificaciones**. Cada auto con documento tiene el botón **Abrir auto**, que abre el
   PDF publicado por el despacho a pantalla completa, con enlaces para descargarlo, abrirlo
   desde el servidor de la Rama Judicial y llegar al portal de consulta.

Además, la pestaña **Novedades** reúne lo nuevo de todos los procesos con filtros (todas,
solo autos, pendientes, estados y avisos, por proceso). Desde la barra superior puede pulsar
**Verificar ahora**, **pausar o reanudar la vigilancia**, hacer una **consulta puntual**,
activar los **avisos del navegador** y seguir el **presupuesto diario** de solicitudes.

**La vigilancia arranca sola al abrir la interfaz** y sigue el horario configurado: de lunes a
viernes a las 07:00, 10:00, 13:00 y 17:00, y nada después de las 18:00, porque los despachos
registran los autos en horario laboral. Si abre el programa cuando ya pasó una de esas horas,
verifica de inmediato. Con la interfaz cerrada no se consulta nada.

Opciones útiles: `--sin-vigilar` abre la interfaz sin arrancar la vigilancia, `--puerto 0`
elige un puerto libre, `--sin-navegador` no abre pestaña. La misma base de datos SQLite sirve
a la interfaz y a los comandos de consola, así que puede combinarlos.

### Sobre el "enlace directo al auto"

El portal de la Rama Judicial es una aplicación de una sola página sin enlaces profundos: al
abrir un proceso la dirección no cambia, así que no existe una URL que lleve a un auto concreto.
La interfaz resuelve esto de tres formas: el botón **Abrir auto** abre en una pestaña propia
el **PDF publicado por el despacho** (obtenido con la misma API del portal) junto con el texto
de la actuación y el enlace de descarga oficial; el **detalle** muestra la actuación completa;
y el botón **Portal** copia el radicado al portapapeles y abre la página oficial para pegarlo.

## Uso desde la consola

1. Cree la configuración y edite `general.agente_usuario` con un correo de contacto real:

```bash
consultor-procesos iniciar-config
```

2. Registre los radicados que quiere vigilar:

```bash
consultor-procesos agregar 11001400300120240012345 --alias "Caso demo"
```

3. Verifique. La primera vez se registra la **línea base** (las actuaciones que ya existen)
   sin notificar; desde la segunda, solo se avisa lo nuevo:

```bash
consultor-procesos verificar
```

4. Para dejarlo corriendo con el horario configurado (de lunes a viernes a las 07:00, 10:00, 13:00
   y 17:00 por defecto; `--intervalo-minutos` para un intervalo fijo):

```bash
consultor-procesos vigilar
```

Otros comandos:

```bash
consultor-procesos consultar 11001400300120240012345 --solo-autos
```

```bash
consultor-procesos consultar 11001400300120240012345 --json --salida consulta.json
```

`--salida` escribe el archivo directamente en UTF-8. Es preferible a redirigir con `>` desde
Windows PowerShell 5.1, que vuelve a codificar la salida de los programas con la página de
códigos regional y estropea las tildes.

```bash
consultor-procesos autos 11001400300120240012345
```

```bash
consultor-procesos historial
```

```bash
consultor-procesos listar
```

```bash
consultor-procesos quitar 11001400300120240012345
```

Salida típica de `verificar` cuando aparece un auto con documento:

```text
2026-09-02 10:30 | Radicado 11001400300120240012345 (Caso demo)
  Despacho: JUZGADO 001 CIVIL MUNICIPAL DE PRUEBA
  1 actuacion(es) nueva(s), 1 auto(s)
  [AUTO] 2026-09-01 #4    Constancia secretarial - AUTO FIJA FECHA PARA AUDIENCIA (con documentos)
         Documento: Auto fija fecha.pdf -> https://consultaprocesos.ramajudicial.gov.co:448/api/v2/Descarga/DocumentoActuacion/501
  Portal: https://consultaprocesos.ramajudicial.gov.co/Procesos/NumeroRadicacion
Radicado                 Estado  Nuevas  Autos  Solic.  Detalle
-----------------------  ------  ------  -----  ------  -----------------------------------
11001400300120240012345  OK      1       1      3       1 actuación(es) nueva(s), 1 auto(s).
Total: 1 radicado(s), 3 solicitud(es) a la fuente, 1 auto(s) nuevo(s).
```

## Cómo detecta los autos

Cada actuación tiene un *tipo* (`actuacion`) y una *anotación* (`anotacion`). En la práctica
muchos despachos registran el tipo genérico "Constancia secretarial" y describen el auto en
la anotación ("AUTO ORDENA REQUERIR..."), así que el detector revisa **ambos** campos:

- Normaliza el texto (quita tildes, colapsa espacios, mayúsculas).
- Busca la palabra completa `AUTO` o `AUTOS`. "AUTOMOTOR" o "AUTORIZA" no cuentan; "AUTO-ADMISORIO" sí.
- Puede ampliarse con `verificacion.palabras_clave`, por ejemplo `["AUTO", "SENTENCIA", "FIJACION ESTADO"]`.

Cada actuación nueva se guarda con su marca `es_auto` y las palabras que coincidieron, de
modo que `consultor-procesos autos <radicado>` lista el historial de autos sin volver a consultar.

## Documentos de las actuaciones

Cuando una actuación trae documentos, el portal expone la lista y la descarga por actuación.
Al detectar un **auto nuevo con documentos**, la verificación pide la lista (una solicitud) y
la incluye en la notificación; la interfaz web permite verlos y descargarlos. Los PDF se
guardan una sola vez en `general.directorio_documentos` (por defecto `documentos/`), así que
volver a abrirlos no cuesta solicitudes. La línea base no descarga documentos, para no
disparar cientos de solicitudes al registrar un proceso antiguo; desde la interfaz se pueden
pedir cuando haga falta.

## Estados, avisos y traslados de los despachos (micrositios)

Cada despacho publica sus estados electrónicos, notificaciones por aviso, traslados, edictos y
autos masivos en el portal **Publicaciones Procesales**, que es a donde apuntan los micrositios
de `ramajudicial.gov.co/web/<despacho>`. El programa identifica el despacho por los 12 primeros
dígitos del radicado (y, en segunda instancia, por el código que informa la ficha de la CPNU) y:

- Revisa cada despacho a lo sumo una vez cada 20 horas, solo en los tipos configurados
  (`publicaciones.tipos`), porque cada consulta al portal pesa cerca de 1 MB.
- Busca sus radicados en el título y el resumen de cada publicación nueva y, si no aparecen,
  descarga los PDF (el estado, los autos del estado) y los lee. Reconoce el radicado completo,
  los 21 dígitos sin instancia y la forma corta año-consecutivo ("2024-00123").
- Cada coincidencia queda en la pestaña **Estados y avisos** de la interfaz, con el fragmento
  donde aparece el radicado, el enlace a la publicación y a sus PDF, y se notifica por los
  mismos canales que los autos.

```bash
consultor-procesos publicaciones --revisar
```

```bash
consultor-procesos publicaciones --despacho 110014003001
```

Desactívelo con `publicaciones.habilitado = false` si no lo necesita.

## Cómo evita sobrecargar el portal

| Mecanismo | Valor por defecto | Para qué |
| --- | --- | --- |
| Identificación honesta | `User-Agent` propio con contacto | Que un administrador pueda escribirle antes de bloquear |
| Limitador de tasa (cubeta de fichas) | 12 solicitudes/min, ráfaga 1 | Ritmo sostenido de una solicitud cada 5 s |
| Pausa entre radicados | 3 s | Respiro adicional entre procesos |
| Reintentos con retroceso exponencial y fluctuación | 4 intentos, 2 s -> 4 s -> 8 s, tope 60 s, respeta `Retry-After` | Recuperarse de 429/5xx sin insistir en ráfaga |
| Cortacircuito | 5 fallos seguidos -> 5 min sin consultar | No seguir golpeando una fuente caída |
| Presupuesto diario | 400 solicitudes | Tope duro aunque haya un error de configuración |
| Huella de cambios | se compara la fecha de última actuación | Una sola solicitud por radicado cuando nada cambió |
| Lectura incremental | se detiene en la primera actuación conocida | Descargar solo las páginas necesarias |
| Una operación a la vez | candado único en la interfaz web | Que verificación, vigilancia y consultas no se solapen |
| Caché de documentos | disco local | Cada PDF se descarga una sola vez |
| Publicaciones por despacho | una revisión cada 20 h, 4 solicitudes/min, tipos acotados | El portal de publicaciones sirve páginas de ~1 MB |
| Horario de verificación | lunes a viernes a las 07:00, 10:00, 13:00 y 17:00; nada después de las 18:00 | Los autos se registran en horario laboral; de noche y en fin de semana no se consulta |
| Fluctuación | hasta 5 min después de la hora (o +/- 20 % en modo por intervalo) | Evitar patrones perfectamente regulares |

Costo aproximado por radicado y verificación: **1 solicitud** si no hubo cambios, 2 a 4 si
los hubo, más una por cada auto nuevo con documentos. La línea base cuesta 1 + una solicitud
por cada 40 actuaciones. Con 50 radicados y las 4 verificaciones diarias del horario se
consumen unas 200 a 300 solicitudes; si necesita más, suba `presupuesto_diario_solicitudes` a conciencia.

## Notificaciones

Se configuran en la sección `[notificaciones]` del TOML y pueden combinarse:

- **Consola**: siempre disponible.
- **Archivo JSON Lines** (`novedades.jsonl`): una línea por actuación nueva, con sus documentos y enlaces; fácil de leer desde Excel, Power BI o un script.
- **Correo SMTP**: la contraseña se lee de la variable de entorno indicada en `contrasena_env`, nunca del archivo.
- **Webhook**: `POST` JSON a la URL indicada (Slack, Teams, n8n, Make, etc.).

Por defecto el correo solo avisa autos; la consola y el archivo registran todas las actuaciones nuevas.

## Llevarlo a otro equipo (USB)

La guía completa, con la lista de archivos, la instalación en el otro equipo y un guion para
la demostración, está en [LEEME_DEMO.md](LEEME_DEMO.md). En resumen:

El entorno `.venv` no es portable, pero todo lo demás sí. Con la interfaz cerrada, ejecute
`preparar_usb.bat` e indique la carpeta de destino (por ejemplo `E:\ConsultorDeProcesos`):
copia el programa, la carpeta `wheels` con las dependencias ya descargadas, la base de datos
con sus procesos, la configuración y los PDF guardados. En el otro equipo:

1. Instale Python 3.11 o superior desde python.org si no lo tiene, marcando
   "Add python.exe to PATH".
2. Copie la carpeta de la USB al disco local (por ejemplo a Documentos).
3. Ejecute `instalar.bat`. Si existe la carpeta `wheels`, instala sin internet.
4. Ejecute `iniciar_interfaz.bat`.

Para vigilar de verdad sí hace falta internet, porque las consultas van a la Rama Judicial.

## De dónde salen los datos

Todo lo que muestra el programa viene del servicio oficial que usa el propio portal
`consultaprocesos.ramajudicial.gov.co` (dirección `.../api/v2`): la búsqueda por radicado, la
ficha, las actuaciones y los documentos. La ficha de cada proceso en la interfaz enlaza esos
datos en bruto (JSON) para que pueda comprobarlos en la fuente. El portal no tiene una página
propia por proceso: existe la ruta `/details-process/{id}`, pero abierta directamente aparece
vacía porque solo se llena tras una búsqueda en la misma sesión.

## Programación en Windows

`verificar` está pensado para correr desde el Programador de tareas; cada ejecución abre la
base, verifica, notifica y termina. Ejemplo cada 4 horas (ajuste las rutas):

```bash
schtasks /Create /SC HOURLY /MO 4 /TN "ConsultorProcesos" /TR "\"C:\ruta\ConsultorDeProcesos\.venv\Scripts\consultor-procesos.exe\" --config \"C:\ruta\ConsultorDeProcesos\consultor_procesos.toml\" verificar"
```

Las alternativas son dejar `vigilar` corriendo en una consola, o simplemente la interfaz
(`web` o `iniciar_interfaz.bat`), que arranca la vigilancia con el horario de `[vigilancia]`
mientras esté abierta.

## Estados de una verificación

| Estado | Significado |
| --- | --- |
| `OK` | Se revisaron actuaciones; puede haber novedades o no. |
| `SIN_CAMBIOS` | La fecha de última actuación no cambió; no se descargaron actuaciones (1 solicitud). |
| `NO_ENCONTRADO` | La fuente no conoce ese radicado. |
| `PRIVADO` | El proceso es privado: la fuente no publica actuaciones. |
| `ERROR` | La fuente respondió con un error definitivo para ese radicado; el lote continúa. |
| `OMITIDO` | La fuente no responde o se agotó el presupuesto; el resto del lote se pospone. |

## Pruebas

```bash
pytest
```

La suite (más de 250 pruebas) no toca la red: el cliente HTTP se prueba con respuestas simuladas
(429 con `Retry-After`, 503 persistente, tiempos de espera, cortacircuito, presupuesto, descarga
de documentos), la interfaz web se ejercita sin puertos y con un servidor real en un puerto
libre, y las esperas usan un reloj inyectado, así que corre en pocos segundos. Hay una prueba
de integración real, desactivada por defecto, que hace exactamente dos solicitudes:

```bash
CONSULTOR_PRUEBAS_EN_VIVO=1 CONSULTOR_RADICADO_PRUEBA=11001400300120240012345 pytest -m en_vivo
```

## Estructura

```text
src/consultor_procesos/
  dominio/          modelos, reglas (detector de autos, huella, novedades), puertos, errores
  aplicacion/       ServicioVigilancia, ServicioPublicaciones (estados y avisos) y Planificador
  adaptadores/
    cpnu/           cliente HTTP cortés y traductor de la API de la CPNU (procesos, actuaciones, documentos)
    publicaciones/  cliente y analizador HTML del portal Publicaciones Procesales (micrositios)
    persistencia/   repositorio SQLite (y uno en memoria para pruebas)
    notificacion/   consola, JSONL, correo, webhook, compuesto, formato compartido
    web/            servidor HTTP local (biblioteca estándar) y la página de la interfaz
  infraestructura/  cortesía (limitador, reintentos, cortacircuito, presupuesto, ventana), solicitante HTTP compartido, PDF y registro
  enlaces.py        URL del portal y de la API oficial
  configuracion.py  carga del TOML y plantilla de ejemplo
  cli.py            interfaz de línea de comandos (incluye el comando web)
tests/              pruebas unitarias, de integración simulada, de CLI y de la interfaz web
docs/ARQUITECTURA.md, docs/FUENTES.md
```

## Consideraciones legales y de datos

- La información de la CPNU es pública, pero contiene nombres de las partes. La base local
  (`consultor_procesos.sqlite`), el archivo `novedades.jsonl` y la carpeta `documentos/` guardan
  anotaciones y PDF que pueden incluirlos: trátelos como información sensible conforme a la
  Ley 1581 de 2012.
- La interfaz web escucha solo en `127.0.0.1` y no tiene autenticación; no la exponga a la red
  sin poner delante un proxy con acceso controlado.
- Use la herramienta para radicados de su interés legítimo (procesos propios o de sus
  clientes), no para barridos masivos.
- Configure `general.agente_usuario` con un contacto real. Es la mejor garantía de que,
  ante cualquier problema, la Rama Judicial pueda avisarle en lugar de bloquearlo.

## Licencia

MIT.
