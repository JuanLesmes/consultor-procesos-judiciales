"""Prueba de integración contra la API real. Desactivada por defecto para no consumir solicitudes.

Ejecución:  CONSULTOR_PRUEBAS_EN_VIVO=1 CONSULTOR_RADICADO_PRUEBA=<23 dígitos> pytest -m en_vivo
Realiza exactamente dos solicitudes (búsqueda + primera página de actuaciones).
"""

import os

import pytest

from consultor_procesos.adaptadores.cpnu.cliente import ClienteCPNU
from consultor_procesos.infraestructura.cortesia import LimitadorTasa

pytestmark = pytest.mark.en_vivo

EN_VIVO = os.environ.get("CONSULTOR_PRUEBAS_EN_VIVO") == "1"
RADICADO_PRUEBA = os.environ.get("CONSULTOR_RADICADO_PRUEBA", "")


@pytest.mark.skipif(
    not (EN_VIVO and RADICADO_PRUEBA),
    reason="Defina CONSULTOR_PRUEBAS_EN_VIVO=1 y CONSULTOR_RADICADO_PRUEBA para consultar la API real.",
)
def test_busqueda_y_actuaciones_reales():
    with ClienteCPNU(
        agente_usuario="ConsultorDeProcesos/0.1 (prueba de integración automatizada)",
        limitador=LimitadorTasa(12, rafaga=2),
    ) as cliente:
        procesos = cliente.buscar_por_radicado(RADICADO_PRUEBA)
        assert procesos, "la API no devolvió procesos para el radicado de prueba"
        pagina = cliente.obtener_actuaciones(procesos[0].id_proceso, 1)
        assert pagina.actuaciones, "la API no devolvió actuaciones"
        assert pagina.actuaciones[0].consecutivo >= pagina.actuaciones[-1].consecutivo, "más recientes primero"
        assert cliente.solicitudes_realizadas == 2
