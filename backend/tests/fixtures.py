"""Small synthetic workbook with known answers for the 4 official demo questions.

Reference date = latest admission = 2026-09-21 12:00.
Expected results (see test_demo_questions.py):
  Q1 ICU occupied today           -> 2 of 3 beds
  Q2 medications < 5 days         -> only "AMOXICILINA 500 MG" (inventory fixture)
  Q3 ER wait last week            -> mean 60.0 min over 2 patients (30 and 90)
  Q4 service with most admissions -> UNIDAD DE CUIDADO INTENSIVO (3 in September)
"""
import pandas as pd

T = pd.Timestamp


def build_frames() -> dict[str, pd.DataFrame]:
    patients = pd.DataFrame([
        # "POPAYÃ\x81N" is double-encoded UTF-8 on purpose (must become "POPAYÁN").
        {"IdPaciente": i, "TipoDocumento": "CC", "NombrePaciente": f"P{i}", "FechaNacimiento": T("1980-01-01"),
         "Sexo": "Femenino" if i % 2 else "Masculino", "Asegurador": "EPS X", "Regimen": "Subsidiado",
         "Departamento": "CAUCA", "Municipio": "POPAYÃ\x81N", "Zona": "Urbana"}
        for i in range(1, 8)
    ])
    admissions = [
        # id, patient, class, route, date, bed group, bed code, triage oid
        (101, 1, "Hospitalario", "Remitido", "2026-09-20 08:00", "UNIDAD DE CUIDADO INTENSIVO", "UCI-1", None),
        (102, 2, "Hospitalario", "Remitido", "2026-09-19 08:00", "UNIDAD DE CUIDADO INTENSIVO", "UCI-2", None),
        (103, 3, "Hospitalario", "Remitido", "2026-09-01 08:00", "UNIDAD DE CUIDADO INTENSIVO", "UCI-3", None),
        (104, 4, "Hospitalario", "Urgencias", "2026-09-21 12:00", "PEDIATRIA", "PED-1", 1),
        (105, 5, "Ambulatorio", "Urgencias", "2026-09-18 10:00", "URGENCIAS", "URG-1", 2),
        (106, 6, "Hospitalario", "Urgencias", "2026-09-02 10:00", "HOSPITALIZACION", "H-1", 3),
        (107, 7, "Hospitalario", "Remitido", "2026-08-15 10:00", "HOSPITALIZACION", "H-2", None),
    ]
    ingresos = pd.DataFrame([
        {"OidIngreso": a, "ConsecutivoIngreso": a * 10, "IdPaciente": p, "ClaseIngreso": c, "ViaIngreso": r,
         "TipoRiesgo": "Enfermedad General", "FechaIngreso": T(d), "FechaHospitalizacion": None,
         "OidTriageA": tri, "CodigoCama": bed, "NombreCama": f"CAMA {bed}", "NombreGrupoCama": g,
         "NombreSubgrupoCama": g, "CodigoDiagnostico": "J189", "NombreDiagnostico": "NEUMONIA"}
        for a, p, c, r, d, g, bed, tri in admissions
    ])
    triage = pd.DataFrame([
        {"OidTriage": 1, "IdPaciente2": 4, "FechaTriage": T("2026-09-21 12:05"), "MotivoConsulta": "FIEBRE",
         "TensionArterial": "110/70", "FrecuenciaCardiaca": 90, "FrecuenciaRespiratoria": 20, "Temperatura": 38.5,
         "CodigoTriage": 7, "ClasificacionTriage": "PEDIATRIA- TRIAGE 2 ( NARANJA)"},
        {"OidTriage": 2, "IdPaciente2": 5, "FechaTriage": T("2026-09-18 10:10"), "MotivoConsulta": "DOLOR",
         "TensionArterial": "120/80", "FrecuenciaCardiaca": 80, "FrecuenciaRespiratoria": 18, "Temperatura": 36.5,
         "CodigoTriage": 24, "ClasificacionTriage": "PROCEDIMIENTOS DIFERIDA- TRIAGE 3"},
        {"OidTriage": 3, "IdPaciente2": 6, "FechaTriage": T("2026-09-02 10:05"), "MotivoConsulta": "TOS",
         "TensionArterial": "120/80", "FrecuenciaCardiaca": 80, "FrecuenciaRespiratoria": 18, "Temperatura": 36.5,
         "CodigoTriage": 11, "ClasificacionTriage": "ADULTOS- TRIAGE 4 ( GRIS)"},
    ])
    atencion = pd.DataFrame([
        {"OidIngreso": 104, "FechaAtencion": T("2026-09-21 12:30")},  # 30 min
        {"OidIngreso": 105, "FechaAtencion": T("2026-09-18 11:30")},  # 90 min
        {"OidIngreso": 106, "FechaAtencion": T("2026-09-02 11:00")},  # 60 min (not last week)
    ])
    servicios = pd.DataFrame([
        {"OidIngreso": 101, "OidS": 1, "CodigoServicio": 890201, "NombreServicio": "CONSULTA", "Cantidad": 1,
         "FechaPrestacion": T("2026-09-21 10:00"), "CodigoAreaServicio": 1, "AreaServicio": "UCI ADULTOS",
         "Especialidad": "MEDICINA INTENSIVA"},
        {"OidIngreso": 102, "OidS": 2, "CodigoServicio": 890201, "NombreServicio": "CONSULTA", "Cantidad": 1,
         "FechaPrestacion": T("2026-09-21 09:00"), "CodigoAreaServicio": 1, "AreaServicio": "UCI ADULTOS",
         "Especialidad": "MEDICINA INTENSIVA"},
        {"OidIngreso": 103, "OidS": 3, "CodigoServicio": 470301, "NombreServicio": "APENDICECTOMIA", "Cantidad": 1,
         "FechaPrestacion": T("2026-09-05 09:00"), "CodigoAreaServicio": 2, "AreaServicio": "QUIROFANOS",
         "Especialidad": "CIRUGIA GENERAL"},
        {"OidIngreso": 105, "OidS": 4, "CodigoServicio": 890701, "NombreServicio": "CONSULTA URGENCIAS", "Cantidad": 1,
         "FechaPrestacion": T("2026-09-18 15:00"), "CodigoAreaServicio": 3, "AreaServicio": "URGENCIAS - CONSULTA",
         "Especialidad": "MEDICINA GENERAL"},
    ])
    medicamentos = pd.DataFrame([
        {"OidIngreso": 101, "OidMI": 1, "CodigoServicio": "M1", "NombreServicio": "AMOXICILINA 500 MG",
         "Cantidad": 3, "FechaPrestacion": T("2026-09-20 09:00"), "AreaServicio": "UCI", "Especialidad": "MEDICINA INTENSIVA"},
        {"OidIngreso": 102, "OidMI": 2, "CodigoServicio": "M2", "NombreServicio": "ACETAMINOFEN 500 MG",
         "Cantidad": 10, "FechaPrestacion": T("2026-09-20 09:00"), "AreaServicio": "UCI", "Especialidad": "MEDICINA INTENSIVA"},
    ])
    cirugia = pd.DataFrame([
        {"ConsecutivoProgramacion": 1, "IdPaciente": 3, "OidIngreso": 103, "CodigoServicio": 470301},  # performed
        {"ConsecutivoProgramacion": 2, "IdPaciente": 3, "OidIngreso": 103, "CodigoServicio": 999999},  # not performed
        {"ConsecutivoProgramacion": 3, "IdPaciente": 9, "OidIngreso": None, "CodigoServicio": 470301},  # unlinked
    ])
    return {
        "Paciente": patients, "Ingresos": ingresos, "Triage": triage, "Atencion": atencion,
        "Servicios": servicios, "MedicamentoInsumo": medicamentos, "ProgramacionCirugia": cirugia,
    }


INVENTORY_FIXTURE = [
    {"_id": "M1", "code": "M1", "name": "AMOXICILINA 500 MG", "stock": 4, "avg_daily_consumption": 2.0,
     "days_of_inventory": 2.0, "reorder_point": 14, "synthetic": True},
    {"_id": "M2", "code": "M2", "name": "ACETAMINOFEN 500 MG", "stock": 100, "avg_daily_consumption": 10.0,
     "days_of_inventory": 10.0, "reorder_point": 70, "synthetic": True},
]
