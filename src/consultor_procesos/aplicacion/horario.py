"""Horario de verificación: horas fijas en días hábiles, con fin de jornada.

Los autos se registran en horario laboral, así que vigilar de noche o el fin de semana solo
gasta solicitudes. El horario define a qué horas se verifica (por ejemplo 07:00, 10:00, 13:00 y
17:00), qué días, y hasta qué hora tiene sentido ejecutar una verificación que quedó pendiente
porque el programa estaba apagado.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DIAS_SEMANA: dict[str, int] = {
    "lun": 0,
    "mar": 1,
    "mie": 2,
    "mié": 2,
    "jue": 3,
    "vie": 4,
    "sab": 5,
    "sáb": 5,
    "dom": 6,
}
NOMBRES_DIA = ("lun", "mar", "mie", "jue", "vie", "sab", "dom")
NOMBRES_DIA_LARGOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_RE_HORA = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$")


def parsear_hora(valor: str | int | float | time) -> time:
    """Acepta "07:00", "7", "17:30" o un entero de horas."""
    if isinstance(valor, time):
        return valor
    if isinstance(valor, bool):
        raise ValueError(f"Hora inválida: {valor!r}")
    if isinstance(valor, (int, float)):
        hora, minuto = int(valor), int(round((valor - int(valor)) * 60))
    else:
        coincidencia = _RE_HORA.match(str(valor))
        if coincidencia is None:
            raise ValueError(f"Hora inválida: {valor!r}; use el formato HH:MM, por ejemplo \"07:00\"")
        hora, minuto = int(coincidencia.group(1)), int(coincidencia.group(2) or 0)
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        raise ValueError(f"Hora fuera de rango: {valor!r}")
    return time(hora, minuto)


def parsear_dia(valor: str | int) -> int:
    """Acepta "lun".."dom" (también "lunes", "Miércoles"...) o 0..6 con lunes = 0."""
    if isinstance(valor, bool):
        raise ValueError(f"Día inválido: {valor!r}")
    if isinstance(valor, int):
        if 0 <= valor <= 6:
            return valor
        raise ValueError(f"Día inválido: {valor!r}; use 0 (lunes) a 6 (domingo)")
    clave = str(valor).strip().lower()[:3]
    if clave not in DIAS_SEMANA:
        raise ValueError(f"Día inválido: {valor!r}; use lun, mar, mie, jue, vie, sab o dom")
    return DIAS_SEMANA[clave]


@dataclass(frozen=True)
class Horario:
    """Horas del día (ordenadas) en las que se verifica, en los días hábiles indicados."""

    horas: tuple[time, ...]
    dias: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    hasta: time | None = None
    festivos: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        if not self.horas:
            raise ValueError("El horario necesita al menos una hora")
        object.__setattr__(self, "horas", tuple(sorted(set(self.horas))))
        object.__setattr__(self, "dias", frozenset(self.dias))
        object.__setattr__(self, "festivos", frozenset(self.festivos))
        if not self.dias:
            raise ValueError("El horario necesita al menos un día de la semana")
        if self.hasta is not None and self.hasta <= self.horas[-1]:
            raise ValueError("'hasta' debe ser posterior a la última hora programada")

    @classmethod
    def desde_texto(
        cls,
        horas: Iterable[str | int | float],
        dias: Iterable[str | int] | None = None,
        hasta: str | None = None,
        festivos: Iterable[str] = (),
    ) -> Horario:
        return cls(
            horas=tuple(parsear_hora(h) for h in horas),
            dias=frozenset(parsear_dia(d) for d in (dias if dias is not None else NOMBRES_DIA[:5])),
            hasta=parsear_hora(hasta) if hasta not in (None, "") else None,
            festivos=frozenset(date.fromisoformat(str(f).strip()) for f in festivos),
        )

    @property
    def fin_jornada(self) -> time:
        """Hora a partir de la cual ya no se ejecuta nada; por defecto, una hora después de la última."""
        if self.hasta is not None:
            return self.hasta
        referencia = date(2000, 1, 3)
        limite = datetime.combine(referencia, self.horas[-1]) + timedelta(hours=1)
        return limite.time() if limite.date() == referencia else time(23, 59, 59)

    def es_dia_habil(self, dia: date) -> bool:
        return dia.weekday() in self.dias and dia not in self.festivos

    def en_jornada(self, momento: datetime) -> bool:
        return self.es_dia_habil(momento.date()) and self.horas[0] <= momento.time() < self.fin_jornada

    def proxima(self, momento: datetime) -> datetime:
        """Primera hora programada estrictamente posterior a `momento`."""
        dia = momento.date()
        for _ in range(400):
            if self.es_dia_habil(dia):
                for hora in self.horas:
                    candidata = datetime.combine(dia, hora)
                    if candidata > momento:
                        return candidata
            dia += timedelta(days=1)
        raise ValueError("El horario no tiene ningún día hábil en el próximo año")

    def ultima(self, momento: datetime) -> datetime | None:
        """Última hora programada igual o anterior a `momento` (None si no hubo ninguna en un año)."""
        dia = momento.date()
        for _ in range(400):
            if self.es_dia_habil(dia):
                for hora in reversed(self.horas):
                    candidata = datetime.combine(dia, hora)
                    if candidata <= momento:
                        return candidata
            dia -= timedelta(days=1)
        return None

    def pendiente(self, ultima_ejecucion: datetime | None, momento: datetime) -> bool:
        """True si, dentro de la jornada, ya pasó una hora programada que no se ha ejecutado."""
        if not self.en_jornada(momento):
            return False
        programada = self.ultima(momento)
        if programada is None:
            return False
        return ultima_ejecucion is None or ultima_ejecucion < programada

    def a_dict(self) -> dict:
        return {
            "horas": [h.strftime("%H:%M") for h in self.horas],
            "dias": [NOMBRES_DIA[d] for d in sorted(self.dias)],
            "hasta": self.fin_jornada.strftime("%H:%M"),
            "festivos": [f.isoformat() for f in sorted(self.festivos)],
            "descripcion": self.descripcion(),
        }

    def descripcion(self) -> str:
        dias = sorted(self.dias)
        if dias == [0, 1, 2, 3, 4]:
            texto_dias = "de lunes a viernes"
        elif dias == list(range(7)):
            texto_dias = "todos los días"
        else:
            texto_dias = "los " + ", ".join(NOMBRES_DIA_LARGOS[d] for d in dias)
        horas = [h.strftime("%H:%M") for h in self.horas]
        texto_horas = horas[0] if len(horas) == 1 else ", ".join(horas[:-1]) + " y " + horas[-1]
        return f"{texto_dias} a las {texto_horas}"
