# Llevar el Consultor de Procesos a la oficina: qué guardar y qué hacer allá

Guía paso a paso para copiar el programa a una USB, instalarlo en otro equipo con Windows y
mostrarlo funcionando. Está pensada para hacerse sin ayuda técnica.

---

## Parte 1. En su computador: qué guardar en la USB

### 1.1 Cierre la interfaz

Si tiene abierta la ventana negra del Consultor, ciérrela (o pulse Ctrl+C dentro de ella).
Así la base de datos queda completa antes de copiarla.

### 1.2 Ejecute `preparar_usb.bat`

1. Conecte la USB y mire qué letra le asignó Windows (por ejemplo `E:`).
2. En la carpeta del proyecto, haga doble clic en **`preparar_usb.bat`**.
3. Cuando pregunte la carpeta de destino, escriba por ejemplo `E:\ConsultorDeProcesos` y pulse Enter.
4. Espere el mensaje "Copia terminada".

Eso copia todo lo necesario (unos 4 MB). Si prefiere hacerlo a mano, esta es la lista:

| Copiar | Para qué |
| --- | --- |
| `src\` | El programa |
| `wheels\` | Las librerías ya descargadas, para instalar sin internet |
| `pyproject.toml` | Descripción del programa; lo necesita el instalador |
| `instalar.bat`, `iniciar_interfaz.bat`, `preparar_usb.bat` | Los lanzadores |
| `consultor_procesos.toml` | Su configuración (horario, puerto, correo de contacto) |
| `consultor_procesos.sqlite` (y, si existen, `-wal` y `-shm`) | Su base de datos: procesos, historial, revisados |
| `documentos\` (si existe) | Los PDF ya descargados |
| `README.md`, `docs\`, `LEEME_DEMO.md` | Documentación |

**No copie `.venv`.** Es el entorno de Python de este equipo y no funciona en otro; el
instalador lo vuelve a crear allá.

### 1.3 Tenga en cuenta

- La base de datos lleva sus radicados reales y los nombres que les puso. Si va a mostrarla
  a terceros, revise antes qué procesos contiene.
- Si en el equipo de la oficina no hay Python, descargue también el instalador en la USB:
  https://www.python.org/downloads/windows/ (versión 3.11 o superior, "Windows installer 64-bit").

### 1.4 Recomendado: deje preparado un proceso con documentos

El botón **Abrir auto** solo aparece en los autos cuyo despacho publicó el PDF. Su proceso
actual no tiene ninguno. Para que la demostración luzca:

1. Antes de copiar, agregue en la interfaz un radicado del que sepa que el juzgado sube los
   autos (uno propio de la firma).
2. Entre a su ficha, abra uno de sus autos con **Abrir auto** y espere a que se vea el PDF.

Con eso el PDF queda guardado en `documentos\` y viaja en la USB: en la oficina se abrirá al
instante, incluso si la red está lenta.

---

## Parte 2. En el equipo de la oficina: instalar

### 2.1 Requisitos

- Windows 10 u 11.
- Python 3.11 o superior. Para saber si está instalado: abra el menú Inicio, escriba `cmd`,
  abra el Símbolo del sistema y escriba `py --version`. Si responde algo como `Python 3.12.x`,
  ya está. Si dice que no se reconoce el comando, instálelo desde la USB o desde python.org
  **marcando la casilla "Add python.exe to PATH"** en la primera pantalla del instalador.
- Internet, solo para que las consultas lleguen a la Rama Judicial. La instalación no lo necesita.

### 2.2 Pasos

1. Copie la carpeta `ConsultorDeProcesos` de la USB al disco del equipo, por ejemplo a
   `Documentos`. No lo ejecute directamente desde la USB: es más lento y algunas USB no
   permiten escribir la base de datos.
2. Entre a la carpeta copiada y haga doble clic en **`instalar.bat`**. Crea el entorno e
   instala las librerías desde la carpeta `wheels`. Tarda menos de un minuto. Si Windows
   muestra "Windows protegió su PC", pulse "Más información" y luego "Ejecutar de todas formas":
   es un archivo por lotes sin firma, no un programa descargado.
3. Cuando diga "Instalación terminada", haga doble clic en **`iniciar_interfaz.bat`**.
4. Se abre el navegador en `http://127.0.0.1:8770/`. Debajo del título debe decir **v0.2.0**.
   Deje la ventana negra abierta mientras use el programa; cerrarla apaga el Consultor.

### 2.3 Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| "No se encontró Python" | Instale Python (2.1) y vuelva a ejecutar `instalar.bat`. |
| "No se pudo abrir el puerto 8770" | Otro programa lo usa. Abra `consultor_procesos.toml`, cambie `puerto = 8770` por `puerto = 8780` y reinicie el .bat. |
| El navegador no abre solo | Abra uno y escriba `http://127.0.0.1:8770/`. |
| La página se ve "vieja" o sin pestañas | Es una pestaña antigua del navegador: pulse Ctrl+F5 o ciérrela y vuelva a entrar. Compruebe que diga v0.2.0. |
| "Solicitudes hoy: 0 / 400" no cambia al verificar | No hay internet o la Rama Judicial no responde. La información ya guardada se sigue viendo. |
| Chip "Vigilancia en pausa" al abrir | Es normal fuera del horario (después de las 18:00 o fin de semana): no consulta hasta la siguiente hora programada. "Verificar ahora" funciona igual. |

---

## Parte 3. Guion para mostrarlo (10 a 15 minutos)

Orden sugerido. Entre paréntesis, la idea que conviene decir en cada paso.

1. **Pantalla de procesos.** Cada tarjeta muestra el nombre del proceso, el radicado, el
   despacho, el tipo y cuántos autos hay por revisar. (Lo que antes exigía entrar al portal
   proceso por proceso se ve de un vistazo.)
2. **Agregar un proceso en vivo.** Escriba un radicado de 23 dígitos, un nombre, y pulse
   "Vigilar este proceso". La interfaz salta a su ficha y lee el historial completo en unos
   segundos. (Una sola consulta cortés a la Rama Judicial por radicado; se ve el contador
   "Solicitudes hoy" subir.)
3. **Ficha del proceso.** Despacho, sujetos, ponente, fechas, y abajo las pestañas Historial,
   Autos, Estados y avisos y Verificaciones. (Toda la historia del expediente en un lugar,
   con los autos resaltados.)
4. **Abrir auto.** En el proceso que dejó preparado, pulse "Abrir auto": se abre el PDF del
   despacho a pantalla completa con los botones Descargar, Abrir en la Rama Judicial y Portal
   de consulta. (El documento sale del servidor oficial; el enlace "Abrir en la Rama Judicial"
   lo demuestra.)
5. **Origen de los datos.** En la ficha, la fila "Origen de los datos" abre en la Rama Judicial
   los datos en bruto de ese proceso. (Transparencia: nada se inventa, todo se puede verificar.)
6. **Estados y avisos.** Pestaña de la ficha o botón "Revisar despacho ahora". (Además del
   expediente, lee los estados electrónicos que el juzgado publica en su micrositio y busca el
   radicado incluso dentro de los PDF. Tarda uno o dos minutos porque cada página pesa 1 MB.)
7. **Marcar revisada.** Pulse en un auto "Marcar revisada": desaparece de pendientes y el
   contador baja. (Control de lo atendido, compartido por todos los que usen ese equipo.)
8. **Novedades.** Pestaña superior con lo nuevo de todos los procesos y filtros. (La vista de
   "qué llegó hoy".)
9. **Horario.** Señale el subtítulo "Verifica de lunes a viernes a las 07:00, 10:00, 13:00 y
   17:00" y el chip "próxima hoy HH:MM". (Se revisa cuatro veces al día en horario laboral,
   sin intervención; si el equipo estaba apagado a esa hora, lo hace al encenderlo.)
10. **Pausar y Verificar ahora.** Muestre que se puede pausar y consultar a mano en cualquier momento.
11. **Consulta puntual.** Un radicado cualquiera sin guardarlo. (Para preguntas sueltas.)

Cierre con lo que **no** hace, porque es parte del valor: no evade CAPTCHA ni se hace pasar
por un navegador, se identifica ante la Rama Judicial con un contacto, y limita sus consultas
(12 por minuto, 400 al día). Es un asistente cortés, no un robot que tumba el portal.

---

## Parte 4. Al volver

- Si en la oficina agregó procesos que quiere conservar, copie de vuelta
  `consultor_procesos.sqlite` (con la interfaz cerrada) a su computador.
- Si el equipo de la oficina se va a quedar con el programa, edite allá la línea
  `agente_usuario` de `consultor_procesos.toml` con el correo de contacto de la firma.
