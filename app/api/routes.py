"""HTTP surface. Thin layer only: parse params, call orchestrator/services,
map errors. All analysis logic lives in the pipeline; all persistence in
services. The /events endpoint reads events.json DIRECTLY and is labeled as
debug-only - the pipeline itself never touches that file (ground-truth
firewall, enforced by tests)."""

import json
import math
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import DATA_DIR
from app.pipeline.orchestrator import run_analysis, run_overview
from app.pipeline.semantic_resolver import AccessDenied
from app.scenarios.evaluate import run_evaluation
from app.scenarios.recovery import run_recovery_sim
from app.services import telemetry
from app.services.feedback_engine import correction_counts, feedback_history, record_feedback

router = APIRouter()

# Last analysis per KPI (for GET /kpis/{id}/evidence without re-running).
_LAST_ANALYSES: dict[str, dict] = {}


def _json_safe(obj):
    """Recursively replace NaN / +/-inf floats with None.

    FastAPI's JSON encoder serializes with allow_nan=False, so a non-finite
    float surfacing from the pipeline (e.g. a zero-denominator CAC ratio)
    would otherwise blow up into an unhandled 500 -> the frontend's generic
    "Access Restricted" card. Replacing them with None keeps the response a
    valid JSON 200 so the UI can render whatever the analysis legitimately
    produced.
    """
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


@router.get("/kpis")
def list_kpis(request: Request):
    cards = run_overview()
    return _json_safe({"as_of_demo_clock": True, "kpis": cards})


@router.get("/kpis/{kpi_id}/analysis")
def analyze(kpi_id: str, request: Request,
            persona: str = Query("cfo", pattern="^(cfo|analyst)$"),
            role: str = Query("cfo", pattern="^(cfo|analyst)$"),
            user_region: str | None = None,
            focus_region: str | None = None):
    try:
        result = run_analysis(kpi_id, role, user_region, focus_region, persona)
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown KPI '{kpi_id}'")

    _LAST_ANALYSES[kpi_id] = result
    facts = result["facts"]
    return _json_safe({
        "insight_id": facts["insight_id"],
        "movement": facts["movement"],
        **result["view"],
        # UI-only extras: the full deterministic bundle either persona needs
        "series": facts["series"],
        "drivers": facts["drivers"],
        "waterfall_identity_passed": facts["waterfall_identity_passed"],
        "evidence": facts["evidence"],
        "confidence": facts["confidence"],
        "actions": [a.as_dict() for a in facts["actions"]],
        "causal_tests": [asdict(r) for r in facts["causal_results"]],
        "restricted_columns": facts["restricted_columns"],
        "freshness": facts["freshness"],
        "narrative_meta": result["narrative_meta"],
        "demo_now": facts["demo_now"],
        "sentiment_context": facts["sentiment_context"],
        "cross_source_context": facts["cross_source_context"],
        "alternative_hypotheses": facts["alternative_hypotheses"],
    })


@router.get("/kpis/{kpi_id}/recovery-sim")
def recovery_sim(kpi_id: str, role: str = Query("cfo", pattern="^(cfo|analyst)$"),
                 user_region: str | None = None,
                 focus_region: str | None = None):
    if kpi_id != "net_revenue":
        raise HTTPException(status_code=404,
                            detail="Recovery simulation currently modeled for net_revenue")
    try:
        return run_recovery_sim(role, user_region, focus_region)
    except AccessDenied as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/kpis/{kpi_id}/evidence")
def evidence(kpi_id: str):
    if kpi_id not in _LAST_ANALYSES:
        raise HTTPException(status_code=404,
                            detail="No analysis run yet for this KPI - call /analysis first")
    res = _LAST_ANALYSES[kpi_id]
    return {
        "kpi_id": kpi_id,
        "evidence": res["registry_payload"],
        "freshness": res["facts"]["freshness"],
        "lineage": res["facts"]["lineage"],
        "note": "Evidence from the most recent /analysis call in this process.",
    }


@router.post("/feedback")
def feedback(request: Request, body: dict):
    """Records feedback. For action='correct' with correction_text, a plain-
    English intent-mapping stage normalizes the text onto the contract driver
    vocabulary (LLM when configured, deterministic keyword scorer otherwise)
    and returns an INTEGRATION RECEIPT showing exactly what changed."""
    from app.contracts.contract_loader import load_contracts
    from app.pipeline.intent_mapper import map_correction

    action = body.get("action", "")
    if action not in ("accept", "reject", "correct"):
        raise HTTPException(status_code=422, detail="action must be accept|reject|correct")

    corrected_driver = body.get("corrected_driver")
    integration = None
    if action == "correct" and body.get("correction_text"):
        mapping = map_correction(body["correction_text"], load_contracts())
        if mapping["matched_driver"]:
            corrected_driver = mapping["matched_driver"]
        integration = mapping

    try:
        out = record_feedback(
            insight_id=body.get("insight_id", ""),
            action=action,
            corrected_driver=corrected_driver,
            kpi_id=body.get("kpi_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if integration is not None:
        matched = integration["matched_driver"]
        out["integration"] = {
            "understood_as": matched,
            "match_confidence": integration["match_confidence"],
            "method_type": integration["method_type"],
            "alternatives_considered": integration["alternatives"],
            "integrated_into": [
                "confidence weights (nudged below)" if out["weights_after"] != out["weights_before"] else None,
                "action ranking tiebreak (max +25%, damped)",
                "correction memory chips",
                "subsequent analyses as analyst-flagged candidate driver",
            ],
            "raw_text": integration["raw_text"],
        }
        out["integration"]["integrated_into"] = [
            x for x in out["integration"]["integrated_into"] if x]
    return out


@router.get("/feedback/history")
def history(limit: int = 20):
    return {"history": feedback_history(limit), "corrections": correction_counts()}


@router.get("/evaluation")
def evaluation():
    """Detector scorecard (multi-seed precision/recall) + PSI drift report.
    Cached per process - the sweep takes a few seconds on first call."""
    return run_evaluation()


@router.get("/audit/{request_id}")
def audit(request_id: str):
    """Full audit trace for one request: stage timings + every evidence item."""
    trace = telemetry.audit_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Unknown request '{request_id}'")
    return trace


@router.get("/telemetry")
def get_telemetry(limit: int = 15, summary: bool = False):
    if summary:
        return {"summary": telemetry.session_totals()}
    return {"requests": telemetry.recent(limit),
            "summary": telemetry.session_totals()}


@router.get("/events")
def planted_events():
    """DEBUG ONLY - ground truth for judges to verify detection quality.
    The pipeline NEVER imports this file (firewall test enforces)."""
    path = Path(DATA_DIR) / "events.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="events.json missing")
    return {
        "warning": "DEBUG GROUND TRUTH - never used by the analysis pipeline",
        "events": json.loads(path.read_text()),
    }
