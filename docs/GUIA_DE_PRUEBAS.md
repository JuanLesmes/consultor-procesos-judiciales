# Guía de funcionamiento y casos de prueba

Qué hace el Consultor de Procesos, cómo probarlo en un computador y una lista de casos de prueba
con su resultado esperado, para comprobar (y mostrar) que funciona. Cada caso indica también cómo
contrastarlo con la fuente oficial, porque todo lo que muestra el programa debe coincidir con lo
que publica la Rama Judicial.

Los resultados de ejemplo son los del proceso **11001418903620250045100**, probado el 5 de
octubre de 2026. Si el juzgado registra actuaciones nuevas, los números cambian; eso también es
parte de la prueba.

## 1. Qué hace

| Función | Qué hace | Dónde se ve |
| --- | --- | --- |
| Vigilar radicados | Guarda los procesos a vigilar (23 dígitos) con un nombre opcional | Pantalla **Procesos** |
| Ficha del proceso | Despacho, departamento, sujetos, tipo, clase, ponente, fecha de radicación, última actuación | Al abrir un proceso |
| Historial completo | Todas las actuaciones, de la más reciente a la más antigua | Pestaña **Historial** |
| Detección de autos | Marca como auto toda actuación cuyo tipo o anotación dice AUTO o AUTOS | Pestaña **Autos** y etiqueta AUTO |
| PDF del auto | Si la Consulta de Procesos publica el documento, lo abre desde el servidor oficial | Botón **Abrir auto** |
| Estados y avisos del despacho | Revisa el micrositio del juzgado (Publicaciones Procesales) y busca el radicado en el título, en el detalle de cada publicación, en los nombres de los PDF y dentro de los PDF | Pestaña **Estados y avisos** |
| Novedades | Lo nuevo de todos los procesos, con filtros y "Marcar revisada" | Pestaña **Novedades** |
| Vigilancia automática | Verifica de lunes a viernes a las 07:00, 10:00, 13:00 y 17:00; nada después de las 18:00 | Encabezado y chip de vigilancia |
| Avisos | Interfaz, correo, webhook y archivo `novedades.jsonl` | Según la configuración |
| Consulta puntual | Consulta un radicado una vez, sin guardarlo | Recuadro **Consulta puntual** |
| Cortesía con el portal | Máximo 12 solicitudes por minuto y 400 al día, reintentos con espera y pausa si el portal falla | "Solicitudes hoy: N / 400" |
| Fallar en voz alta | Si la Rama Judicial cambia el formato de sus datos, el proceso queda en ERROR en vez de seguir a ciegas | Pestaña **Verificaciones** y monitor |
| Origen de los datos | Enlaces a los datos en bruto de la Rama Judicial | Fila "Origen de los datos" de la ficha |

Lo que **no** hace: no evade CAPTCHA ni bloqueos, no consulta TYBA ni SAMAI, no lee procesos
privados (la fuente no publica sus actuaciones) y no inventa el PDF de un auto que el juzgado no
publicó.

### Para qué es el correo de contacto

El programa se identifica ante la Rama Judicial en cada solicitud con un texto (el *User-Agent*)
como `ConsultorDeProcesos/0.3.1 (vigilancia de radicados propios; contacto: sistemas@firma.com)`.
Si a los administradores del portal les preocupa el tráfico, pueden escribir a ese correo en vez de
bloquear la dirección IP del servidor, lo que dejaría sin vigilancia a toda la firma. Es la práctica
habitual de los programas automáticos responsables. El correo no se usa para iniciar sesión ni se
envía a ningún otro lugar. Conviene que sea un buzón institucional de la firma. Sin él el programa
funciona igual, pero el monitor lo señala como aviso.

## 2. Prepararlo en su computador

### Opción rápida (sin Docker)

En PowerShell, dentro de la carpeta del proyecto:

```bash
.venv\Scripts\pip install -e ".[dev]"
```

```bash
$env:CONSULTOR_CONTACTO = "sistemas@sufirma.com"
```

```bash
.venv\Scripts\consultor-procesos --bd prueba.sqlite web
```

Abra `http://127.0.0.1:8770`. En su propio computador no pide contraseña. `--bd prueba.sqlite` usa
una base aparte, para que la prueba no se mezcle con sus procesos; para empezar de cero, cierre el
programa (Ctrl+C) y borre `prueba.sqlite`.

Para los comandos de consola de los casos de prueba, abra **otra** ventana de PowerShell en la misma
carpeta y use siempre la misma base, por ejemplo:

```bash
.venv\Scripts\consultor-procesos --bd prueba.sqlite historial
```

### Opción igual al servidor (Docker)

Siga la sección "Probarlo en su computador con Docker" del [README](../README.md): `.env` con un
usuario, `docker compose up --build` y entrar a `http://127.0.0.1:8770` con ese usuario. Los
comandos de consola se ejecutan anteponiendo `docker compose exec consultor`.

## 3. Casos de prueba

Marque cada caso como cumplido cuando el resultado coincida con el esperado.

### CP-01. Agregar un proceso y leer su ficha

- **Pasos:** en **Agregar proceso**, escriba el radicado y un nombre (por ejemplo "Proceso de
  prueba") y pulse **Vigilar este proceso**. Espere a que termine la lectura (unos segundos).
- **Esperado:** la interfaz abre la ficha con el despacho, el departamento, los sujetos, el tipo, la
  clase, el ponente, la fecha de radicación y la última actuación. La primera lectura es la **línea
  base**: guarda lo que ya existía sin avisarlo como nuevo.
- **Ejemplo (11001418903620250045100):** Juzgado 036 de Pequeñas Causas y Competencia Múltiple de
  Bogotá; tipo "De Ejecución", clase "Ejecutivo Singular"; radicado el 2025-05-06; última
  actuación 2026-09-30. Costó 3 solicitudes a la Consulta de Procesos.
- **Contraste:** en https://consultaprocesos.ramajudicial.gov.co/Procesos/NumeroRadicacion busque el
  mismo número y compare los datos del proceso.

### CP-02. Historial completo

- **Pasos:** pestaña **Historial** de la ficha.
- **Esperado:** el mismo número de actuaciones que el portal, de la más reciente a la más antigua,
  con fecha, tipo y anotación.
- **Ejemplo:** 12 actuaciones, de la #12 "Fijación estado" (30/09/2026) a la #1 "Radicación de
  Proceso" (06/05/2025).
- **Contraste:** pestaña "Actuaciones" del proceso en el portal. Revise al menos las tres primeras y
  el total.

### CP-03. Detección de autos

- **Pasos:** pestaña **Autos**.
- **Esperado:** solo las actuaciones que son autos; "Al despacho" o "Fijación estado" no cuentan.
- **Ejemplo:** 4 autos: "Auto inadmite demanda" (13/06/2025), "Auto decreta medida cautelar" y "Auto
  libra mandamiento ejecutivo" (05/09/2025), "Auto pone en conocimiento" (30/09/2026).
- **Contraste:** en el portal, las actuaciones cuyo tipo o anotación dice "Auto".

### CP-04. Estados y avisos del despacho (micrositio)

- **Pasos:** pestaña **Estados y avisos** → **Revisar despacho ahora**. Tarda de 1 a 3 minutos,
  porque cada página del portal pesa cerca de 1 MB y se consulta despacio a propósito.
- **Esperado:** las publicaciones del despacho que mencionan el radicado, con el lugar donde aparece
  y el botón **Abrir PDF**, que abre el documento desde el servidor de la Rama Judicial.
- **Ejemplo:** "Notificación por Estado No. 091 de 01 de octubre de 2026"; el radicado aparece en el
  documento **"2025-00451 NO TIENE EN CUENTA NOTIF.pdf"** (forma corta año-consecutivo). Este
  juzgado publica cada auto como un PDF aparte en el detalle del estado.
- **Contraste:** en https://publicacionesprocesales.ramajudicial.gov.co filtre por el despacho
  `110014189036`, abra el estado 091 y busque el documento 2025-00451.

### CP-05. Origen de los datos

- **Pasos:** en la ficha, fila "Origen de los datos", pulse **ficha** y **actuaciones**.
- **Esperado:** se abren en una pestaña los datos en bruto (JSON) del servicio oficial de la Rama
  Judicial, los mismos que usa el programa.

### CP-06. Llega un auto nuevo (simulado)

No hace falta esperar a que el juzgado publique algo: el programa puede "olvidar" las últimas
actuaciones en su base local para que la siguiente verificación las traiga como nuevas. No cambia
nada en la Rama Judicial.

- **Pasos:** en la segunda ventana de PowerShell:

```bash
.venv\Scripts\consultor-procesos --bd prueba.sqlite simular-novedad 11001418903620250045100 --cantidad 2
```

  Luego, en la interfaz, pulse **Verificar ahora** (o ejecute `verificar --radicado ...`).
- **Esperado:** el resultado dice "2 actuación(es) nueva(s), 1 auto(s)"; las dos aparecen en
  **Novedades** como pendientes, el auto resaltado; el contador de pendientes sube; si el aviso por
  correo está configurado, llega el correo; y se agrega una línea en `novedades.jsonl`.
- **Ejemplo:** nuevas la #12 "Fijación estado" y la #11 "Auto pone en conocimiento" (auto), con 3
  solicitudes.

### CP-07. Marcar revisada

- **Pasos:** en **Novedades** o en la ficha, pulse **Marcar revisada** en una actuación.
- **Esperado:** sale de pendientes y el contador baja en 1. Es compartido: otro usuario lo ve igual.

### CP-08. Verificar sin cambios cuesta una sola solicitud

- **Pasos:** pulse **Verificar ahora** dos veces seguidas y mire la pestaña **Verificaciones**.
- **Esperado:** la segunda queda en "SIN_CAMBIOS" con **1** solicitud. Excepción: si la última
  actuación tiene 5 días o menos, el programa relee las actuaciones por si el juzgado registró dos el
  mismo día, así que la verificación sale "OK, sin novedades" con 2 o 3 solicitudes.
- **Ejemplo:** con última actuación del 30/09/2026, a partir del 6 de octubre la segunda verificación
  ya es "SIN_CAMBIOS".

### CP-09. Horario y vigilancia automática

- **Pasos:** mire el subtítulo del encabezado y el chip de vigilancia; pulse **Pausar** y
  **Reanudar**.
- **Esperado:** "Verifica de lunes a viernes a las 07:00, 10:00, 13:00 y 17:00 (nada después de las
  18:00)" y la próxima hora programada. En pausa no se consulta nada salvo "Verificar ahora". Si el
  programa arranca dentro de la jornada después de una hora que no corrió, verifica de inmediato.

### CP-10. Consulta puntual

- **Pasos:** recuadro **Consulta puntual**, escriba otro radicado y pulse **Consultar**.
- **Esperado:** muestra el proceso y sus actuaciones sin agregarlo a la vigilancia.

### CP-11. Abrir el PDF de un auto desde la Consulta de Procesos

- **Pasos:** use un proceso cuyo juzgado publique los autos en la Consulta de Procesos (en el portal
  la actuación tiene el ícono de documento). En la pestaña **Autos**, pulse **Abrir auto**.
- **Esperado:** se abre el PDF a pantalla completa, con los botones Descargar, Abrir en la Rama
  Judicial y Portal de consulta.
- **Nota:** en el proceso de ejemplo ninguna actuación trae documento en la Consulta de Procesos, así
  que el botón no aparece. Ahí el auto llega por el estado del micrositio (CP-04).

### CP-12. Cortesía con el portal

- **Pasos:** observe "Solicitudes hoy: N / 400" antes y después de verificar.
- **Esperado:** el contador sube solo lo necesario: una línea base cuesta unas 3 solicitudes por
  proceso y la revisión de estados de un despacho entre 4 y 15, a un ritmo de 4 por minuto.

### CP-13. Pruebas automáticas

```bash
.venv\Scripts\python -m pytest
```

- **Esperado:** "372 passed, 1 skipped" o más. No consultan la Rama Judicial: simulan sus respuestas,
  incluidos los casos difíciles (portal caído, bloqueos, campos renombrados, 120 actuaciones nuevas
  de golpe).

### CP-14. Prueba contra la API real

```bash
$env:CONSULTOR_PRUEBAS_EN_VIVO = "1"; $env:CONSULTOR_RADICADO_PRUEBA = "11001418903620250045100"; .venv\Scripts\python -m pytest -m en_vivo
```

- **Esperado:** "1 passed". Hace exactamente 3 solicitudes y confirma que el programa sigue
  entendiendo las respuestas oficiales (búsqueda, actuaciones y ficha).

### CP-15. Seguridad (en el servidor o con Docker)

- **Pasos:** entre a la dirección del servidor sin usuario, con una contraseña equivocada y con la
  correcta. Desde su computador también puede ejecutar:

```bash
python scripts/vigilar.py https://consultor.sufirma.com --usuario monitor --clave "..."
```

- **Esperado:** sin usuario el navegador pide credenciales; con una equivocada vuelve a pedirlas; tras
  10 intentos fallidos esa dirección IP queda bloqueada 15 minutos. El script termina con "Todo en
  orden": HTTPS, redirección desde http://, acceso protegido, CSRF y vigilancia al día.

### CP-16. Monitor y despliegue (en el servidor)

- **Pasos:** en GitHub, **Actions → Monitor → Run workflow**.
- **Esperado:** termina en verde con el informe "Todo en orden". Si se pausa la vigilancia y se vuelve
  a correr, termina en rojo y abre un issue con la etiqueta `monitor`; al reanudar y correrlo otra
  vez, el issue se cierra solo.

### Lo que se prueba solo con pruebas automáticas

Hay situaciones que no se pueden provocar en vivo sin afectar el portal. Están cubiertas por CP-13:

| Situación | Comportamiento comprobado |
| --- | --- |
| La Rama Judicial renombra un campo (por ejemplo `idRegActuacion`) | El proceso queda en ERROR con el nombre del campo; nada se guarda a medias |
| El portal falla (503, sin respuesta) | Reintenta con espera; si sigue, pospone el resto del lote sin insistir |
| El portal responde 403 (posible bloqueo) | Deja de consultar un rato (cortacircuito) |
| Una consulta de prueba del cortacircuito recibe un 404 | El cortacircuito se cierra; ya no queda bloqueado hasta reiniciar |
| Llegan 200 actuaciones entre dos verificaciones | Las lee todas hasta empalmar con lo conocido |
| Se agota el presupuesto diario | No pasa del tope ni con varios procesos a la vez |
| La ruta del portal de publicaciones da 404 | Usa la ruta alternativa (caso real del 4 de octubre de 2026) |

## 4. Resultado de la prueba del 5 de octubre de 2026

| Caso | Resultado |
| --- | --- |
| CP-01 Ficha | Cumple: despacho, tipo, clase, ponente y fechas iguales al portal; 3 solicitudes |
| CP-02 Historial | Cumple: 12 actuaciones |
| CP-03 Autos | Cumple: 4 autos |
| CP-04 Estados | Cumple, tras dos ajustes hechos en la prueba: leer el detalle de cada estado y usar la ruta alternativa del portal. Encuentra el estado 091 con el PDF "2025-00451 NO TIENE EN CUENTA NOTIF.pdf" |
| CP-06 Auto nuevo simulado | Cumple: 2 nuevas, 1 auto, notificación en consola y en `novedades.jsonl` |
| CP-11 PDF desde la Consulta de Procesos | No aplica a este proceso: el juzgado no publica documentos allí |
| CP-13 Pruebas automáticas | Cumple: 372 passed |
| CP-14 API real | Cumple: 1 passed |
