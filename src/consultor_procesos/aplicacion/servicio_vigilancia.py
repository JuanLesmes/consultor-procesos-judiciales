"""Casos de uso: registrar radicados, verificarlos con cortesía, notificar novedades y servir documentos.

Estrategia de verificación (pensada para gastar el mínimo de solicitudes):

1. Una sola búsqueda por radicado devuelve, por cada despacho donde exista, la fecha de la
   última actuación. Con eso se calcula una "huella".
2. Si la huella no cambió, la última actuación es antigua (más de `dias_gracia`) y hace
   menos de `horas_refresco_completo` que se leyeron actuaciones, se termina ahí: una
   sola solicitud y estado SIN_CAMBIOS.
3. Si algo cambió (o toca el refresco periódico) se leen las actuaciones página a página,
   más recientes primero, deteniéndose en la primera ya conocida. No hay un tope menor para
   las lecturas incrementales: si entre dos verificaciones llegaron muchas actuaciones, se
   sigue leyendo hasta empalmar con lo conocido (hasta `max_paginas_inicial`), para que no
   queden huecos. Si ni así se empalma, el resultado lo advierte.
4. Las actuaciones nuevas se evalúan con el detector de autos, se guardan y se notifican.
   Para los autos que traen documentos se pide la lista (una solicitud por auto) para que
   la notificación enlace directamente al PDF. La primera lectura de un radicado es la
   "línea base": se guarda pero no se notifica, salvo que se pida explícitamente; en ella
   también se lee la ficha del proceso (despacho, tipo, clase, ponente y código de despacho
   para las publicaciones procesales), que se relee cada vez que aparecen actuaciones nuevas.
5. Al final del lote, si está configurado, se revisan las publicaciones procesales
   (estados, avisos, traslados) de los despachos involucrados.
"""

from __future__ import annotations

import logging
import mimetypes
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from ..dominio.errores import (
    DocumentoNoEncontrado,
    ErrorConsultor,
    ErrorFuente,
    FuenteNoDisponible,
    PresupuestoAgotado,
    ProcesoNoEncontrado,
    RespuestaInesperada,
)
from ..dominio.modelos import (
    Actuacion,
    CoincidenciaPublicacion,
    ConsultaProceso,
    DescargaDocumento,
    DetalleProceso,
    Documento,
    EstadoVerificacion,
    EventoNovedades,
    Novedad,
    Proceso,
    ProcesoVigilado,
    ResultadoPublicaciones,
    ResultadoVerificacion,
)
from ..dominio.puertos import FuenteProcesos, Notificador, Repositorio
from ..dominio.reglas import (
    DetectorAutos,
    calcular_huella,
    calcular_novedades,
    fecha_ultima_actuacion,
    ordenar_actuaciones,
    seleccionar_procesos,
    validar_radicado,
)
from .servicio_publicaciones import ServicioPublicaciones

log = logging.getLogger(__name__)


@dataclass
class OpcionesVerificacion:
    dias_gracia: int = 5
    horas_refresco_completo: int = 24
    max_paginas_inicial: int = 20  # tope de páginas (de 40) por lectura, en la línea base y en las siguientes
    pausa_entre_procesos_segundos: float = 3.0
    notificar_existentes: bool = False
    listar_documentos_de_autos: bool = True
    descubrir_despachos: bool = True  # leer la ficha (tipo, clase, ponente, código de despacho) en la línea base y al haber cambios


@dataclass
class LecturaActuaciones:
    actuaciones: list[Actuacion] = field(default_factory=list)
    # False si se agotó el máximo de páginas sin llegar al final ni a una actuación conocida.
    completa: bool = True
    paginas: int = 0


def leer_actuaciones(
    fuente: FuenteProcesos,
    id_proceso: int,
    max_paginas: int,
    detener_si: Callable[[Actuacion], bool] | None = None,
) -> LecturaActuaciones:
    """Lee las páginas de actuaciones (más recientes primero) hasta `detener_si`, el final o `max_paginas`."""
    lectura = LecturaActuaciones()
    limite = max(1, max_paginas)
    while lectura.paginas < limite:
        lectura.paginas += 1
        resultado = fuente.obtener_actuaciones(id_proceso, lectura.paginas)
        for actuacion in resultado.actuaciones:
            if detener_si is not None and detener_si(actuacion):
                return lectura
            lectura.actuaciones.append(actuacion)
        if not resultado.hay_mas or not resultado.actuaciones:
            return lectura
    lectura.completa = False
    log.warning(
        "Se alcanzó el máximo de %d páginas para el proceso %s; puede haber actuaciones sin leer.",
        limite,
        id_proceso,
    )
    return lectura


def recorrer_actuaciones(
    fuente: FuenteProcesos,
    id_proceso: int,
    max_paginas: int,
    detener_si: Callable[[Actuacion], bool] | None = None,
) -> Iterator[Actuacion]:
    """Como `leer_actuaciones`, pero devuelve solo las actuaciones."""
    yield from leer_actuaciones(fuente, id_proceso, max_paginas, detener_si).actuaciones


def tipo_contenido_por_nombre(nombre: str) -> str:
    tipo, _ = mimetypes.guess_type(nombre or "")
    return tipo or "application/octet-stream"


def extension_por_tipo(tipo: str) -> str:
    if tipo == "application/pdf":
        return ".pdf"
    return mimetypes.guess_extension(tipo or "") or ""


class ServicioVigilancia:
    def __init__(
        self,
        fuente: FuenteProcesos,
        repositorio: Repositorio,
        notificador: Notificador,
        detector: DetectorAutos | None = None,
        opciones: OpcionesVerificacion | None = None,
        ahora: Callable[[], datetime] = datetime.now,
        dormir: Callable[[float], None] = time.sleep,
        directorio_documentos: Path | None = None,
        publicaciones: ServicioPublicaciones | None = None,
    ) -> None:
        self._fuente = fuente
        self._repositorio = repositorio
        self._notificador = notificador
        self._detector = detector or DetectorAutos()
        self._opciones = opciones or OpcionesVerificacion()
        self._ahora = ahora
        self._dormir = dormir
        self._directorio_documentos = Path(directorio_documentos) if directorio_documentos else None
        self._publicaciones = publicaciones
        self.ultimos_resultados_publicaciones: list[ResultadoPublicaciones] = []

    @property
    def opciones(self) -> OpcionesVerificacion:
        return self._opciones

    @property
    def fuente(self) -> FuenteProcesos:
        return self._fuente

    @property
    def publicaciones(self) -> ServicioPublicaciones | None:
        return self._publicaciones

    # --- gestión de radicados ------------------------------------------------------------

    def agregar(self, radicado: str, alias: str | None = None) -> ProcesoVigilado:
        radicado = validar_radicado(radicado)
        existente = self._repositorio.obtener_vigilado(radicado)
        if existente is not None:
            existente.activo = True
            if alias:
                existente.alias = alias
            self._repositorio.guardar_vigilado(existente)
            return existente
        nuevo = ProcesoVigilado(radicado=radicado, alias=alias or None, creado_en=self._ahora())
        self._repositorio.guardar_vigilado(nuevo)
        return nuevo

    def quitar(self, radicado: str) -> bool:
        return self._repositorio.eliminar_vigilado(validar_radicado(radicado))

    def listar(self, solo_activos: bool = True) -> list[ProcesoVigilado]:
        return self._repositorio.listar_vigilados(solo_activos=solo_activos)

    def autos_registrados(self, radicado: str) -> list[Novedad]:
        return self._repositorio.listar_actuaciones(validar_radicado(radicado), solo_autos=True)

    def actuaciones_registradas(self, radicado: str) -> list[Novedad]:
        return self._repositorio.listar_actuaciones(validar_radicado(radicado), solo_autos=False)

    # --- novedades registradas ------------------------------------------------------------

    def novedades_recientes(
        self,
        limite: int = 50,
        solo_autos: bool = False,
        radicado: str | None = None,
        solo_pendientes: bool = False,
    ) -> list[Novedad]:
        radicado_normalizado = validar_radicado(radicado) if radicado else None
        return self._repositorio.listar_novedades_recientes(
            limite=limite, solo_autos=solo_autos, radicado=radicado_normalizado, solo_pendientes=solo_pendientes
        )

    def novedad(self, id_registro: int) -> Novedad | None:
        return self._repositorio.obtener_novedad(id_registro)

    def contar_pendientes(self, solo_autos: bool = False) -> int:
        return self._repositorio.contar_pendientes(solo_autos=solo_autos)

    def marcar_revisada(self, id_registro: int, revisada: bool = True) -> bool:
        return self._repositorio.marcar_revisada(id_registro, revisada)

    # --- documentos -----------------------------------------------------------------------

    def documentos_de(self, id_registro: int, forzar: bool = False) -> list[Documento]:
        """Lista los documentos de una actuación; consulta la fuente solo la primera vez (o si se fuerza)."""
        if not forzar:
            guardados = self._repositorio.listar_documentos(id_registro)
            if guardados is not None:
                return guardados
        documentos = self._fuente.listar_documentos(id_registro)
        self._repositorio.guardar_documentos(id_registro, documentos, self._ahora())
        return documentos

    def descargar_documento(self, id_documento: int) -> DescargaDocumento:
        """Devuelve el contenido de un documento registrado, usando la caché en disco si existe."""
        documento = self._repositorio.obtener_documento(id_documento)
        if documento is None:
            raise DocumentoNoEncontrado(
                f"El documento {id_documento} no está registrado; liste primero los documentos de la actuación."
            )
        ruta_guardada = self._repositorio.ruta_local_documento(id_documento)
        if ruta_guardada and Path(ruta_guardada).is_file():
            nombre = documento.nombre or Path(ruta_guardada).name
            return DescargaDocumento(
                id_documento=id_documento,
                contenido=Path(ruta_guardada).read_bytes(),
                nombre=nombre,
                tipo_contenido=tipo_contenido_por_nombre(nombre),
            )
        descarga = self._fuente.descargar_documento(id_documento)
        nombre = descarga.nombre or documento.nombre or f"documento_{id_documento}"
        tipo = descarga.tipo_contenido
        if tipo in ("", "application/octet-stream"):
            tipo = tipo_contenido_por_nombre(nombre)
        if not Path(nombre).suffix:
            nombre += extension_por_tipo(tipo)
        if self._directorio_documentos is not None and descarga.contenido:
            self._directorio_documentos.mkdir(parents=True, exist_ok=True)
            ruta = self._directorio_documentos / f"{id_documento}{Path(nombre).suffix or '.bin'}"
            ruta.write_bytes(descarga.contenido)
            self._repositorio.registrar_descarga(id_documento, str(ruta), self._ahora())
        return replace(descarga, nombre=nombre, tipo_contenido=tipo)

    # --- verificación ---------------------------------------------------------------------

    def verificar_todos(
        self, radicados: list[str] | None = None, incluir_publicaciones: bool = True
    ) -> list[ResultadoVerificacion]:
        """Verifica los radicados activos en orden, con pausa entre ellos, y luego sus publicaciones.

        Si la fuente deja de responder (reintentos agotados, cortacircuito abierto) o se
        agota el presupuesto diario, el resto del lote se marca OMITIDO en vez de insistir.
        """
        vigilados = self.listar()
        if radicados:
            filtro = {validar_radicado(r) for r in radicados}
            vigilados = [v for v in vigilados if v.radicado in filtro]

        resultados: list[ResultadoVerificacion] = []
        motivo_detencion: str | None = None
        for indice, vigilado in enumerate(vigilados):
            if motivo_detencion is not None:
                omitido = ResultadoVerificacion(
                    vigilado.radicado, EstadoVerificacion.OMITIDO, self._ahora(), mensaje=motivo_detencion
                )
                self._repositorio.registrar_verificacion(omitido)
                resultados.append(omitido)
                continue
            if indice > 0 and self._opciones.pausa_entre_procesos_segundos > 0:
                self._dormir(self._opciones.pausa_entre_procesos_segundos)
            try:
                resultados.append(self.verificar(vigilado))
            except (FuenteNoDisponible, PresupuestoAgotado) as exc:
                motivo_detencion = f"Lote detenido: {exc}"
                log.error(motivo_detencion)
                omitido = ResultadoVerificacion(
                    vigilado.radicado, EstadoVerificacion.OMITIDO, self._ahora(), mensaje=str(exc)
                )
                self._repositorio.registrar_verificacion(omitido)
                resultados.append(omitido)

        self.ultimos_resultados_publicaciones = []
        if incluir_publicaciones and self._publicaciones is not None and vigilados and motivo_detencion is None:
            actualizados = [self._repositorio.obtener_vigilado(v.radicado) or v for v in vigilados]
            self.ultimos_resultados_publicaciones = self._publicaciones.revisar(actualizados)
            por_radicado: dict[str, list[CoincidenciaPublicacion]] = {}
            for resultado_pub in self.ultimos_resultados_publicaciones:
                for coincidencia in resultado_pub.coincidencias:
                    por_radicado.setdefault(coincidencia.radicado, []).append(coincidencia)
            for resultado in resultados:
                resultado.publicaciones.extend(por_radicado.get(resultado.radicado, []))
        return resultados

    def verificar(self, vigilado: ProcesoVigilado) -> ResultadoVerificacion:
        """Verifica un radicado. Propaga FuenteNoDisponible y PresupuestoAgotado para que el lote decida."""
        momento = self._ahora()
        solicitudes_antes = self._fuente.solicitudes_realizadas
        try:
            resultado = self._verificar(vigilado, momento)
        except (FuenteNoDisponible, PresupuestoAgotado):
            raise
        except ErrorConsultor as exc:
            log.warning("Error verificando el radicado %s: %s", vigilado.radicado, exc)
            resultado = ResultadoVerificacion(
                vigilado.radicado, EstadoVerificacion.ERROR, momento, mensaje=str(exc)
            )
        resultado.solicitudes = self._fuente.solicitudes_realizadas - solicitudes_antes
        self._repositorio.registrar_verificacion(resultado)
        return resultado

    def _verificar(self, vigilado: ProcesoVigilado, momento: datetime) -> ResultadoVerificacion:
        procesos = seleccionar_procesos(self._fuente.buscar_por_radicado(vigilado.radicado), vigilado.radicado)
        vigilado.ultima_verificacion = momento

        if not procesos:
            self._repositorio.guardar_vigilado(vigilado)
            return ResultadoVerificacion(
                vigilado.radicado,
                EstadoVerificacion.NO_ENCONTRADO,
                momento,
                mensaje="La fuente no devolvió ningún proceso con ese radicado.",
            )

        huella = calcular_huella(procesos)
        publicos = [p for p in procesos if not p.es_privado]
        if not publicos:
            self._actualizar_identidad(vigilado, procesos, huella)
            self._repositorio.guardar_vigilado(vigilado)
            return ResultadoVerificacion(
                vigilado.radicado,
                EstadoVerificacion.PRIVADO,
                momento,
                procesos=procesos,
                mensaje="El proceso es privado: la fuente no publica sus actuaciones.",
            )

        if self._puede_omitir(vigilado, huella, procesos, momento):
            self._actualizar_identidad(vigilado, procesos, huella)
            if self._opciones.descubrir_despachos and vigilado.ficha_leida_en is None:
                # Radicado registrado con una versión anterior: se completa su ficha una sola vez.
                self._leer_ficha(vigilado, publicos, momento)
            self._repositorio.guardar_vigilado(vigilado)
            return ResultadoVerificacion(
                vigilado.radicado,
                EstadoVerificacion.SIN_CAMBIOS,
                momento,
                procesos=procesos,
                mensaje="La fecha de última actuación no cambió; no se descargaron actuaciones.",
            )

        conocidos = self._repositorio.ids_actuaciones_conocidas(vigilado.radicado)
        es_linea_base = not vigilado.inicializado or not conocidos
        detener = None if es_linea_base else (lambda a: a.id_registro in conocidos)
        despachos = {p.id_proceso: p.despacho for p in publicos}

        recolectadas: list[Actuacion] = []
        incompleta = False
        for proceso in publicos:
            lectura = leer_actuaciones(self._fuente, proceso.id_proceso, self._opciones.max_paginas_inicial, detener)
            recolectadas.extend(lectura.actuaciones)
            incompleta = incompleta or not lectura.completa
        nuevas = calcular_novedades(recolectadas, conocidos)
        novedades = [self._detector.evaluar(a, despachos.get(a.id_proceso or -1, "")) for a in nuevas]
        if not es_linea_base and self._opciones.listar_documentos_de_autos:
            novedades = [self._adjuntar_documentos(n) for n in novedades]
        if self._opciones.descubrir_despachos and (es_linea_base or nuevas or vigilado.ficha_leida_en is None):
            # La ficha se lee en la línea base y se refresca solo cuando el expediente se movió:
            # así se detecta un cambio de despacho (apelación, cambio de competencia) sin gastar
            # una solicitud en cada verificación.
            self._leer_ficha(vigilado, publicos, momento)

        self._repositorio.guardar_novedades(novedades, momento)
        vigilado.ultima_lectura_actuaciones = momento
        vigilado.inicializado = True
        self._actualizar_identidad(vigilado, procesos, huella)
        self._repositorio.guardar_vigilado(vigilado)

        if novedades and (not es_linea_base or self._opciones.notificar_existentes):
            self._notificador.notificar(
                EventoNovedades(
                    radicado=vigilado.radicado,
                    alias=vigilado.alias,
                    procesos=procesos,
                    novedades=novedades,
                    momento=momento,
                    es_linea_base=es_linea_base,
                )
            )

        autos = sum(1 for n in novedades if n.es_auto)
        if es_linea_base:
            mensaje = f"Línea base: {len(novedades)} actuación(es) registradas, {autos} auto(s)."
        elif novedades:
            mensaje = f"{len(novedades)} actuación(es) nueva(s), {autos} auto(s)."
        else:
            mensaje = "Actuaciones revisadas; sin novedades."
        if incompleta:
            mensaje += (
                f" Atención: se leyeron {self._opciones.max_paginas_inicial} páginas sin llegar al final"
                + ("" if es_linea_base else " ni a una actuación ya registrada; puede faltar historial intermedio")
                + "."
            )
        return ResultadoVerificacion(
            vigilado.radicado,
            EstadoVerificacion.OK,
            momento,
            procesos=procesos,
            novedades=novedades,
            mensaje=mensaje,
            es_linea_base=es_linea_base,
        )

    def _adjuntar_documentos(self, novedad: Novedad) -> Novedad:
        """Para un auto con documentos, pide la lista a la fuente (una solicitud). Los fallos no frenan la verificación."""
        if not (novedad.es_auto and novedad.actuacion.con_documentos):
            return novedad
        try:
            documentos = self._fuente.listar_documentos(novedad.actuacion.id_registro)
        except RespuestaInesperada:
            raise
        except ErrorFuente as exc:
            log.warning(
                "No se pudo listar los documentos de la actuación %s: %s", novedad.actuacion.id_registro, exc
            )
            return novedad
        return replace(novedad, documentos=tuple(documentos))

    def _leer_ficha(self, vigilado: ProcesoVigilado, procesos: list[Proceso], momento: datetime) -> None:
        """Pide la ficha de cada proceso (una solicitud por proceso) y guarda lo que sirve para presentarlo.

        De la ficha salen el tipo y la clase del proceso, el ponente y el código del despacho
        (12 dígitos), necesario para revisar las publicaciones procesales del tribunal cuando el
        proceso está en segunda instancia, cuyo código no coincide con el que encabeza el radicado.
        """
        codigos = list(vigilado.despachos)
        ficha_tomada = False
        for proceso in procesos:
            try:
                detalle = self._fuente.obtener_detalle(proceso.id_proceso)
            except RespuestaInesperada:
                raise
            except ErrorFuente as exc:
                log.warning("No se pudo leer el detalle del proceso %s: %s", proceso.id_proceso, exc)
                continue
            vigilado.ficha_leida_en = momento
            codigo = (detalle.codigo_despacho or "").strip()
            if codigo and codigo != vigilado.radicado[:12] and codigo not in codigos:
                codigos.append(codigo)
            if not ficha_tomada:
                vigilado.tipo_proceso = detalle.tipo_proceso.strip() or vigilado.tipo_proceso
                vigilado.clase_proceso = detalle.clase_proceso.strip() or vigilado.clase_proceso
                vigilado.ponente = detalle.ponente.strip() or vigilado.ponente
                if detalle.despacho.strip():
                    vigilado.despacho = detalle.despacho.strip()
                ficha_tomada = True
        vigilado.despachos = tuple(codigos)

    @staticmethod
    def _actualizar_identidad(vigilado: ProcesoVigilado, procesos: list[Proceso], huella: str) -> None:
        vigilado.id_proceso = procesos[0].id_proceso
        vigilado.huella = huella
        vigilado.fecha_ultima_actuacion = fecha_ultima_actuacion(procesos)
        principal = next((p for p in procesos if not p.es_privado), procesos[0])
        # El despacho lo manda la ficha (se relee cuando el expediente se mueve); la búsqueda solo lo aporta si aún no hay.
        vigilado.despacho = vigilado.despacho or principal.despacho.strip()
        vigilado.departamento = principal.departamento.strip() or vigilado.departamento
        vigilado.sujetos = principal.sujetos.strip() or vigilado.sujetos
        vigilado.fecha_proceso = principal.fecha_proceso or vigilado.fecha_proceso

    def _puede_omitir(
        self, vigilado: ProcesoVigilado, huella: str, procesos: list[Proceso], momento: datetime
    ) -> bool:
        if not vigilado.inicializado or vigilado.huella != huella:
            return False
        ultima = fecha_ultima_actuacion(procesos)
        if ultima is not None and (momento.date() - ultima).days <= self._opciones.dias_gracia:
            return False
        if vigilado.ultima_lectura_actuaciones is None:
            return False
        refresco = timedelta(hours=self._opciones.horas_refresco_completo)
        if momento - vigilado.ultima_lectura_actuaciones >= refresco:
            return False
        return True

    # --- consulta puntual -----------------------------------------------------------------

    def consultar(
        self, radicado: str, incluir_detalle: bool = True, max_paginas: int | None = None
    ) -> ConsultaProceso:
        """Consulta un radicado sin persistir nada. Útil para revisar un proceso una sola vez."""
        radicado = validar_radicado(radicado)
        procesos = seleccionar_procesos(self._fuente.buscar_por_radicado(radicado), radicado)
        if not procesos:
            raise ProcesoNoEncontrado(f"No se encontró ningún proceso con el radicado {radicado}.")
        detalles: list[DetalleProceso] = []
        novedades: list[Novedad] = []
        limite = max_paginas or self._opciones.max_paginas_inicial
        for proceso in procesos:
            if proceso.es_privado:
                continue
            if incluir_detalle:
                try:
                    detalles.append(self._fuente.obtener_detalle(proceso.id_proceso))
                except (FuenteNoDisponible, RespuestaInesperada):
                    raise
                except ErrorFuente as exc:
                    log.warning("No se pudo leer el detalle del proceso %s: %s", proceso.id_proceso, exc)
            actuaciones = list(recorrer_actuaciones(self._fuente, proceso.id_proceso, limite))
            novedades.extend(self._detector.evaluar(a, proceso.despacho) for a in ordenar_actuaciones(actuaciones))
        return ConsultaProceso(radicado=radicado, procesos=procesos, detalles=detalles, novedades=novedades)
