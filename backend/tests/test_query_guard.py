from datetime import datetime

import pytest

from app.services.query_guard import (
    QueryValidationError, enforce_privacy, sanitize_results, validate_query,
)

AGG = "aggregate"


def _agg(*stages, collection="admissions"):
    return {"collection": collection, "operation": AGG, "pipeline": list(stages)}


@pytest.mark.parametrize("query", [
    {"collection": "admissions", "operation": "find", "filter": {"$where": "sleep(100)"}},
    _agg({"$lookup": {"from": "users", "as": "u"}}),
    _agg({"$unionWith": "users"}),
    _agg({"$collStats": {}}),
    _agg({"$currentOp": {}}),
    _agg({"$out": "x"}),
    _agg({"$facet": {"a": [{"$merge": "x"}]}}),
    _agg({"$match": {"$expr": {"$gt": [{"$function": {"body": "1", "args": [], "lang": "js"}}, 0]}}}),
    _agg({"$group": {"_id": None, "x": {"$accumulator": {}}}}),
    _agg({"$project": {"n": "$patient.name"}}),
    _agg({"$group": {"_id": "$patient.birth_date"}}),
    _agg({"$group": {"_id": "$triage.chief_complaint"}}),
    _agg({"$group": {"_id": "$diagnosis.code"}}),
    _agg({"$group": {"_id": "$patient"}}),
    _agg({"$group": {"_id": None, "all": {"$push": "$$ROOT"}}}),
    {"collection": "users", "operation": "find"},
    {"collection": "admissions", "operation": "delete"},
])
def test_dangerous_queries_are_blocked(query):
    with pytest.raises(QueryValidationError):
        validate_query(query)


def test_unknown_fields_are_rejected():
    with pytest.raises(QueryValidationError, match="Unknown field"):
        validate_query({"collection": "admissions", "operation": "find", "filter": {"grupo_cama": "UCI"}})
    with pytest.raises(QueryValidationError, match="does not exist at this stage"):
        validate_query(_agg({"$group": {"_id": "$bed.group", "n": {"$sum": 1}}}, {"$sort": {"total": -1}}))


def test_valid_queries_pass_and_limit_is_capped():
    q = validate_query({**_agg({"$match": {"admission_date": {"$gte": datetime(2026, 9, 1)}}},
                               {"$group": {"_id": "$bed.group", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}), "limit": 10_000})
    assert q["limit"] == 200
    validate_query({"collection": "admissions", "operation": "find",
                    "filter": {"services": {"$elemMatch": {"specialty": "PEDIATRA"}}}})


def test_privacy_is_enforced_for_find_and_aggregate():
    find = enforce_privacy(validate_query({"collection": "admissions", "operation": "find"}))
    assert find["projection"]["patient.name"] == 0 and find["projection"]["services"] == 0
    inclusion = enforce_privacy(validate_query({"collection": "admissions", "operation": "find", "projection": {"patient": 1}}))
    assert "patient.name" not in inclusion["projection"] and inclusion["projection"]["patient.sex"] == 1
    agg = enforce_privacy(validate_query(_agg({"$match": {"currently_admitted": True}})))
    assert agg["pipeline"][-1]["$project"]["triage.chief_complaint"] == 0


def test_sanitize_results_removes_pii_recursively():
    rows = [{"patient": {"name": "X", "sex": "F", "patient_id": 1}, "diagnosis": {"code": "J18", "chapter": "respiratory"},
             "nested": [{"birth_date": "1990-01-01", "ok": 1}]}]
    assert sanitize_results(rows) == [{"patient": {"sex": "F"}, "diagnosis": {"chapter": "respiratory"}, "nested": [{"ok": 1}]}]
