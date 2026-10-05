# Inventario de fuentes de consulta de la Rama Judicial

Relevamiento hecho el 2 de septiembre de 2026 sobre los sitios oficiales donde se publican
procesos, actuaciones, autos y notificaciones en Colombia. Para cada uno se indica qué ofrece,
si se puede consultar de forma automática **sin evadir ninguna protección**, y qué hace el
Consultor de Procesos con él.

| # | Fuente | Qué publica | Acceso automático | Estado en el programa |
| --- | --- | --- | --- | --- |
| 1 | **CPNU** · Consulta de Procesos Nacional Unificada (`consultaprocesos.ramajudicial.gov.co`) | Procesos y actuaciones de los despachos que reportan a Justicia XXI y a TYBA (civil, familia, laboral, penal, administrativo de instancia), con documentos por actuación | API JSON pública sin CAPTCHA | **Implementado** (fuente principal) |
| 2 | **Publicaciones Procesales** (`publicacionesprocesales.ramajudicial.gov.co`), a donde apuntan los micrositios de cada despacho | Estados electrónicos, notificaciones por aviso, traslados, edictos, autos masivos, sentencias, fijaciones, remates, reparto, entradas al despacho | HTML público sin CAPTCHA; filtro por código de despacho (12 dígitos), tipo y fechas; cada página pesa cerca de 1 MB | **Implementado** (revisión por despacho con lectura de PDF) |
| 3 | **Micrositios de los despachos** (`ramajudicial.gov.co/web/<despacho>/estados-electronicos`, `/autos`, `/avisos-a-la-comunidad`, `/traslados-especiales-y-ordinarios`...) | Menú del despacho: estados, autos, avisos, comunicaciones, edictos, traslados | Son páginas de contenido estático que remiten al portal anterior; los documentos viven en Publicaciones Procesales | **Cubierto** a través de la fuente 2 |
| 4 | **TYBA** · Justicia XXI Web (`procesojudicial.ramajudicial.gov.co/Justicia21`) | Procesos de los despachos migrados a Justicia XXI Web | Formulario con reCAPTCHA | **No se automatiza**. Sus datos ya están en la CPNU (fuente 1) |
| 5 | **SIUGJ** · Sistema Integrado Único de Gestión Judicial (`siugj.ramajudicial.gov.co`) | Consulta de procesos y publicaciones de los distritos que ya usan SIUGJ | reCAPTCHA en la consulta y en las publicaciones | **No se automatiza** |
| 6 | **SAMAI** · Consejo de Estado, tribunales y juzgados administrativos (`samai.consejodeestado.gov.co`) | Procesos de la jurisdicción de lo contencioso administrativo | La búsqueda por radicado es JSON sin CAPTCHA, pero el detalle con las actuaciones exige una verificación "No soy un robot" | **No se automatiza**. Solo sería posible confirmar existencia, ponente y vigencia, sin autos |
| 7 | **Corte Suprema de Justicia** (`procesos.ramajudicial.gov.co/procesoscs`) | Procesos de casación y tutela de la Corte Suprema | Formulario clásico ASP.NET con detector de robots (campo trampa), sin CAPTCHA visible | **Pendiente de evaluar**. El sitio manifiesta no querer automatización; se recomienda preguntar antes de integrarlo |
| 8 | **Corte Constitucional** (`corteconstitucional.gov.co/secretaria/consulta-y-tramite-de-procesos/tutela`) | Expedientes de tutela y constitucionalidad en revisión | Aplicación de una sola página cuyos endpoints no son públicos ni documentados | **Pendiente de evaluar** |
| 9 | **Notificaciones electrónicas por correo** (Ley 2213 de 2022) | Autos y providencias notificados al correo de las partes | No es un sitio consultable | Fuera de alcance; el correo del usuario ya las recibe |

## Cómo se decide qué integrar

1. Solo se usan canales públicos que no exigen CAPTCHA ni verificación humana. Donde existe
   una, la respuesta correcta es no automatizar, no esquivarla.
2. Se prefiere la interfaz que el propio portal usa (API JSON) y, si no la hay, HTML servido en
   el servidor con parámetros estables.
3. El costo para el servidor se estima antes de integrar: la CPNU cuesta unos KB por consulta;
   Publicaciones Procesales cerca de 1 MB, por eso se revisa a lo sumo una vez cada 20 horas
   por despacho y solo en los tipos configurados.

## Detalle de la fuente 2: Publicaciones Procesales

- Portlet Liferay `PublicacionesEfectosProcesalesPortletV2`. La lista filtrada se obtiene con
  `GET /web/publicaciones-procesales/inicio` y parámetros con prefijo
  `_<portlet>_INSTANCE_<instancia>_`: `idStructure` (tipo), `idDespacho` (código de 12 dígitos),
  `fechaInicio`, `fechaFin`, `action=filterStructures`, `delta` (tamaño de página) y `cur` (página).
- Cada publicación trae: título con enlace al detalle (`articleId` estable), categorías
  (tipo, departamento, municipio, entidad, especialidad, despacho), fecha de publicación y un
  resumen con los enlaces a los PDF (por ejemplo "ESTADO" y "AUTOS").
- El código de despacho coincide con los 12 primeros dígitos del radicado, y la ficha de detalle
  de la CPNU (`codDespachoCompleto`) da el código del despacho de segunda instancia.
- Tipos observados (identificadores de estructura): Notificaciones por Estados 6098957,
  Notificaciones por Aviso 6098977, Notificaciones 6098981, Traslados especiales y ordinarios
  6098965, Autos masivo 6098961, Edictos 6098953, Fijaciones 6098973, Sentencias 16709563,
  Acciones de Tutela 6098993, Incidente de Desacato 6099005, Comunicaciones jurídicas 6098985,
  Control de legalidad 6098989, Entradas al despacho 10498720, Informes de Acumulación 6099001,
  Oficios 6098969, Remates 6098997, Reparto 25326533, Avisos 9045223.
- Búsqueda del radicado dentro de las publicaciones: se une cualquier grupo de dígitos separado
  por guiones, puntos o espacios y se buscan tres formas, de la más a la menos precisa: el
  radicado completo, los 21 primeros dígitos (sin instancia) y el par año-consecutivo
  ("2024-00123"), este último válido porque la publicación ya está filtrada por despacho.

## Detalle de la fuente 6: SAMAI

- `POST /Vistas/Casos/Jprocesos.ashx/buscar` con JSON (`tipoBusqueda: "radicado"`, `criterio`,
  `corporacion`, `pagina`, `tamanoPagina`) devuelve `items` con radicado, despacho, ponente, clase,
  fecha de ingreso, vigencia y `urlDetalle`. El catálogo `GET .../Jprocesos.ashx/corporaciones`
  lista Consejo de Estado (`1100103`), tribunales y juzgados administrativos.
- `urlDetalle` (`/Vistas/Casos/list_procesos.aspx?guid=...`) pide resolver "No soy un robot" para
  mostrar las actuaciones. Ahí termina lo que se puede automatizar.
