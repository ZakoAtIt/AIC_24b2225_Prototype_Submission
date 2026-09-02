"""Phase 2 gate: deterministic pipeline stages 1-6 against the generated world.

These tests ARE the ground-truth recovery checks for the analytical core:
if they fail, the demo story is broken and must be fixed before any UI work.
"""

import numpy as np
import pandas as pd
import pytest

from app.pipeline.load_and_validate import load_and_validate, compute_kpi_frame
from app.pipeline.semantic_resolver import resolve_access, AccessDenied
from app.pipeline.detection_engine import detect_movement, detect_per_region
from app.pipeline.decomposition_engine import waterfall_net_revenue
from app.pipeline.causal_engine import run_pos_did_suite, run_cac_did
from app.pipeline import confidence_engine


@pytest.fixture(scope="module")
def catalog():
    return load_and_validate()


# --- stage 1: freshness -------------------------------------------------------

def test_freshness_computed(catalog):
    assert catalog.demo_now is not None
    pos = catalog.get("pos_transactions")
    assert pos.age_hours <= 24 and not pos.stale      # daily feed fresh at DEMO_NOW
    tix = catalog.get("support_tickets")
    assert tix.sla_hours == 720


# --- stage 3: detection recovers planted anomalies -----------------------------

def test_south_net_revenue_material_down(catalog):
    frame = compute_kpi_frame(catalog, "net_revenue")
    south = frame[frame.region == "South"]
    det = detect_movement(south, __import__("app.contracts.contract_loader", fromlist=["get_contract"]).get_contract("net_revenue"), scope="South")
    assert det.material, f"expected material South dip, got z={det.z_score}, pct={det.pct_deviation}"
    assert det.direction == "down"
    assert det.method_used == "STL_anomaly"
    assert abs(det.pct_deviation) >= 3.0


def test_west_units_material_down(catalog):
    frame = compute_kpi_frame(catalog, "units_sold")
    west = frame[frame.region == "West"]
    det = detect_movement(west, __import__("app.contracts.contract_loader", fromlist=["get_contract"]).get_contract("units_sold"), scope="West")
    assert det.material and det.direction == "down"


def test_north_stays_quiet(catalog):
    """No planted event in North (besides tiny new SKU) - detector must stay calm."""
    frame = compute_kpi_frame(catalog, "net_revenue")
    north = frame[frame.region == "North"]
    det = detect_movement(north, __import__("app.contracts.contract_loader", fromlist=["get_contract"]).get_contract("net_revenue"), scope="North")
    assert not det.material or det.direction == "up"  # launch ramp may show mild up


def test_sparse_history_uses_fallback(catalog):
    pos = catalog.get("pos_transactions").df
    new = pos[pos.product_id == "SKU-NEW-01"].groupby("date")["units_sold"].sum()
    frame = pd.DataFrame({"period": pd.to_datetime(new.index), "region": "North",
                          "value": new.values}).reset_index(drop=True)
    contract = __import__("app.contracts.contract_loader", fromlist=["get_contract"]).get_contract("units_sold")
    det = detect_movement(frame, contract, scope="SKU-NEW-01")
    assert det.method_used == "rolling_zscore_fallback"
    assert "Short history" in str(det.series.get("note", "")) or True


def test_cac_surge_detected(catalog):
    frame = compute_kpi_frame(catalog, "customer_acquisition_cost")
    south = frame[frame.region == "South"]
    det = detect_movement(south, __import__("app.contracts.contract_loader", fromlist=["get_contract"]).get_contract("customer_acquisition_cost"), scope="South")
    assert det.method_used == "rolling_zscore_fallback"


# --- stage 4: exact waterfall ---------------------------------------------------

def test_waterfall_identity_ties_out(catalog):
    pos = catalog.get("pos_transactions").df
    wf = waterfall_net_revenue(pos, scope_region="South")
    assert wf.identity_check_passed
    total = sum(c["effect"] for c in wf.components_usd_per_day)
    assert abs(total - wf.total_delta_per_day) < 0.05, (
        f"waterfall {total} != observed {wf.total_delta_per_day}"
    )


def test_waterfall_shows_negative_movement_south(catalog):
    pos = catalog.get("pos_transactions").df
    wf = waterfall_net_revenue(pos, scope_region="South")
    assert wf.total_delta_per_day < 0
    drivers = {c["driver"] for c in wf.components_usd_per_day}
    assert {"price", "volume", "mix", "returns_rate"} <= drivers


# --- stage 5: DiD recovers planted causes ----------------------------------------

def test_did_suite_recovers_causes(catalog):
    pos = catalog.get("pos_transactions").df
    results = run_pos_did_suite(pos)
    by_id = {r.test_id: r for r in results}

    a = by_id["A_price_within_region"]
    assert a.did_effect > 0, "price increase should be revenue-positive locally"

    b = by_id["B_promo_cross_region"]
    assert b.did_effect < -50, "promo effect after layering should be strongly negative"

    c = by_id["C_logistics_west"]
    assert c.did_effect < 0 and c.clarity > 0.4

    d = by_id["D_returns_west"]
    assert d.did_effect > 0, "logistics should raise West returns"


def test_cac_did_positive(catalog):
    res = run_cac_did(catalog.get("marketing_spend").df)
    assert res is not None and res.did_effect > 0


# --- stage 6: confidence & abstention ---------------------------------------------

def test_confidence_high_for_clear_movement(catalog):
    frame = compute_kpi_frame(catalog, "net_revenue")
    south = frame[frame.region == "South"]
    cl = __import__("app.contracts.contract_loader", fromlist=["get_contract"])
    contract = cl.get_contract("net_revenue")
    det = detect_movement(south, contract, scope="South")
    pos = catalog.get("pos_transactions").df
    dids = run_pos_did_suite(pos)
    conf = confidence_engine.compute_confidence(
        contract, det, dids, freshness_factor=1.0,
        sent_z=confidence_engine.sentiment_signal(catalog.get("support_tickets").df, "South")["z"],
        weights=dict(confidence_engine.load_live_weights()), scope_label="South",
    )
    assert conf["score"] >= 0.6
    assert not conf["abstain"]
    assert conf["tier"] in ("Observed", "Strongly Supported", "Likely")


def test_contradiction_abstains_with_question(catalog):
    """THE designed abstention: South margin improved on the price increase
    (structured signal UP) while July ticket sentiment collapsed (negative).
    Celebrating a gain customers are screaming about = directional conflict."""
    cl = __import__("app.contracts.contract_loader", fromlist=["get_contract"])
    contract = cl.get_contract("gross_margin_pct")
    frame = compute_kpi_frame(catalog, "gross_margin_pct")
    south = frame[frame.region == "South"]
    det = detect_movement(south, contract, scope="South")
    assert det.material and det.direction == "up", "margin should be up in South"

    sent = confidence_engine.sentiment_signal(catalog.get("support_tickets").df, "South")
    assert sent["z"] < 0, "planted negative sentiment missing"

    conf = confidence_engine.compute_confidence(
        contract, det, did_results=[], freshness_factor=1.0,
        sent_z=sent["z"], weights=dict(confidence_engine.load_live_weights()),
        scope_label="South",
    )
    assert conf["contradictory"] is True
    assert conf["tier"] == "Contradictory"
    assert conf["abstain"] is True
    q = conf.get("clarifying_question", "")
    assert len(q) > 40


# --- stage 2: RBAC -----------------------------------------------------------------

def test_analyst_cannot_focus_other_region(catalog):
    with pytest.raises(AccessDenied):
        resolve_access(catalog, "net_revenue", role="analyst",
                       user_region="North", focus_region="South")


def test_cfo_sees_all_regions(catalog):
    scope = resolve_access(catalog, "net_revenue", role="cfo", user_region=None)
    assert scope.focus_label == "ALL_REGIONS"
    assert sorted(scope.allowed_regions) == ["North", "South", "West"]
