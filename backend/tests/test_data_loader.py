from app.services.text_utils import fix_mojibake, icd10_chapter, triage_level


def test_fix_mojibake():
    assert fix_mojibake("POPAYÃ\x81N") == "POPAYÁN"
    assert fix_mojibake("PATÃ\x8dA") == "PATÍA"
    assert fix_mojibake("INTERNACIÃ“N") == "INTERNACIÓN"
    assert fix_mojibake("ya estÃ¡ bien â€“ ok") == "ya está bien – ok"
    assert fix_mojibake("POPAYÁN") == "POPAYÁN"  # correct text is untouched


def test_triage_level_from_classification():
    assert triage_level("PEDIATRIA EXTENDIDO- TRIAGE 4 ( GRIS)") == 4
    assert triage_level("-TRIAGE 1 ( ROJO)") == 1
    assert triage_level("TRIAGE II") == 2
    assert triage_level("SIN CLASIFICAR") is None


def test_icd10_chapter():
    assert icd10_chapter("J189") == "respiratory"
    assert icd10_chapter("D649") == "blood" and icd10_chapter("D259") == "neoplasms"


def test_transform_derived_fields(bundle):
    docs = {d["_id"]: d for d in bundle.admissions}
    assert docs[104]["triage"]["level"] == 2 and docs[104]["wait_minutes"] == 30.0
    assert docs[101]["currently_admitted"] and not docs[103]["currently_admitted"]
    assert docs[103]["surgeries_scheduled"] == 2 and docs[103]["surgeries_performed"] == 1
    assert docs[101]["patient"]["municipality"] == "POPAYÁN"
    assert docs[101]["triage"] is None
    assert all(d["etl_run_id"] == "test-run" for d in bundle.admissions)
    assert bundle.quality["surgery_rows_without_admission"] == 1
    assert all(item["synthetic"] for item in bundle.inventory)
