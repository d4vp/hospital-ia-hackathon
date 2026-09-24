"""Inferential reports and dashboard analytics on a larger, seeded synthetic dataset
(the small fixture only reaches the "insufficient data" branches)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.services import kpi_service, report_service
from app.services.analytics import Frames

REF = pd.Timestamp("2026-09-21 12:00")
GROUPS = ["UNIDAD DE CUIDADO INTENSIVO", "PEDIATRIA", "HOSPITALIZACION", "URGENCIAS"]


@pytest.fixture(scope="module")
def frames() -> Frames:
    rng = np.random.default_rng(42)
    rows = []
    for day in range(70):
        date_ = REF.normalize() - pd.Timedelta(days=69 - day)
        respiratory = 3 + day // 5  # clear upward trend -> significant regression
        for i in range(12 + respiratory):
            admitted = date_ + pd.Timedelta(hours=int(rng.integers(0, 24)), minutes=int(rng.integers(0, 60)))
            if admitted > REF:
                continue
            night = admitted.hour >= 19 or admitted.hour < 7
            level = int(rng.integers(1, 6))
            er = i % 2 == 0
            stay = pd.Timedelta(hours=float(rng.uniform(4, 120)))
            rows.append({
                "admission_id": len(rows) + 1, "admission_date": admitted, "admission_class": "Hospitalario",
                "admission_route": "Urgencias" if er else "Remitido",
                "wait_minutes": float(rng.gamma(2, 25 + 15 * night + 5 * level)) if er else np.nan,
                "shift": "night" if night else "day", "estimated_discharge_date": admitted + stay,
                "currently_admitted": admitted + stay >= REF - pd.Timedelta(hours=24),
                "primary_specialty": "MEDICINA INTERNA", "surgeries_scheduled": 1, "surgeries_performed": int(i % 3 != 0),
                "bed_group": GROUPS[i % len(GROUPS)], "triage_level": level, "sex": "Femenino",
                "age_group": "30-44", "regime": "Subsidiado", "insurer": "EPS X",
                "diagnosis_chapter": "respiratory" if i < respiratory else "digestive",
                "length_of_stay_days": stay.total_seconds() / 86400,
            })
    adm = pd.DataFrame(rows)
    adm["triage_level"] = adm["triage_level"].astype("Int64")
    days = pd.date_range(REF.normalize() - pd.Timedelta(days=60), REF.normalize())
    usage = pd.DataFrame([{"date": d, "code": f"M{k}", "name": f"MED {k}", "quantity": k * 3, "lines": k}
                          for d in days for k in range(1, 6)])
    demand = pd.DataFrame([{"date": d, "specialty": s, "area": "AREA", "lines": 5, "quantity": 6, "admissions": 4}
                           for d in days for s in ("PEDIATRIA", "CIRUGIA")])
    inventory = pd.DataFrame([{"code": "M1", "name": "MED 1", "stock": 3, "avg_daily_consumption": 3.0,
                               "days_of_inventory": 1.0, "reorder_point": 21}])
    capacity = pd.DataFrame({"bed_group": GROUPS, "beds": [10, 20, 60, 15]})
    return Frames(admissions=adm, capacity=capacity, inventory=inventory, medication_usage=usage,
                  service_demand=demand, reference_date=REF, data_start=adm["admission_date"].min(), run_id="synthetic")


def test_all_report_sections(frames):
    report = report_service.build_inferential_report(frames, "es")
    wait = report["wait_time_ci"]
    assert wait["n"] > 30 and wait["ci95_mean"][0] < wait["mean"] < wait["ci95_mean"][1]
    assert report["occupancy_ci"] and all("conclusion" in row for row in report["occupancy_ci"])
    assert report["month_comparison"]["mann_whitney"]["p_value"] >= 0
    trends = report["trends"]["trends"]
    assert any(t["category"] == "respiratory" and t["direction"] == "up" for t in trends)
    factors = {f["factor"] for f in report["root_cause"]["findings"]}
    assert factors == {"shift", "triage_level", "daily_volume"}
    english = report_service.build_inferential_report(frames, "en", ["root_cause"])
    assert list(english) == ["reference_date", "root_cause"] and "p" in english["root_cause"]["findings"][0]["conclusion"]


async def test_on_demand_sections_are_cached(frames):
    first = await report_service.build_report_on_demand(frames, "es", ["wait_time_ci", "trends"])
    second = await report_service.build_report_on_demand(frames, "es", ["trends", "wait_time_ci"])
    assert first["sections"] == second["sections"] == ["wait_time_ci", "trends"]
    assert first["trends"] is second["trends"]  # memoized object, not recomputed


def test_dashboard_and_patients(frames):
    board = kpi_service.dashboard(frames, date(2026, 8, 1), date(2026, 9, 21), None, None)
    cards = board["cards"]
    assert cards["admissions"] > 100 and 0 < cards["occupancy_pct"] and cards["low_stock_items"] == 1
    assert board["wait"]["by_triage"] and board["occupancy_trend"]["daily"] and board["top_medications"]
    filtered = kpi_service.dashboard(frames, None, None, "PEDIATRIA", "MEDICINA INTERNA")
    assert filtered["cards"]["admissions"] > 0
    assert {row["bed_group"] for row in filtered["occupancy_now"]} == {"PEDIATRIA"}
    options = kpi_service.filter_options(frames)
    assert options["bed_groups"] == sorted(GROUPS)
    page = kpi_service.patients(frames, None, None, None, None, 2, 10)
    assert page["page"] == 2 and len(page["rows"]) == 10 and "name" not in page["rows"][0]


def test_alert_rules_on_realistic_data(frames):
    from app.services.alert_service import compute_alerts

    alerts = compute_alerts(frames, "es")
    kinds = {a["type"] for a in alerts}
    assert "inventory" in kinds and "er_wait" in kinds
    assert alerts == sorted(alerts, key=lambda a: {"critical": 0, "high": 1, "medium": 2}[a["severity"]])
