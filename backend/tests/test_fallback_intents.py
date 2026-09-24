import pytest

from app.services.fallback_agent import detect_intent, detect_params


@pytest.mark.parametrize("question,intent", [
    ("¿Cuántas camas de UCI están ocupadas hoy?", "occupancy"),
    ("¿Cuáles son los medicamentos con menos de 5 días de inventario?", "low_inventory"),
    ("¿Cuál es el tiempo de espera promedio en urgencias en la última semana?", "er_wait"),
    ("¿Qué servicio tiene más pacientes ingresados este mes?", "top_service"),
    ("Which service has the most admitted patients this month?", "top_service"),
    ("medicamentos de menor rotación", "least_meds"),
    ("cirugías realizadas vs programadas", "surgeries"),
    ("espera por nivel de triage", "wait_by_triage"),
    ("¿y en pediatría?", None),
])
def test_detect_intent(question, intent):
    assert detect_intent(question) == intent


def test_detect_params():
    assert detect_params("¿y en pediatría?") == {"bed_group": "PEDIATRIA"}
    assert detect_params("menos de 3 días de inventario")["days"] == 3
    assert detect_params("¿y el mes pasado?")["period"] == "prev_month"
