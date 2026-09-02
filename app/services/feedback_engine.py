"""Human feedback loop.

POST /feedback stores accept/reject/correct and nudges the LIVE confidence
weights in DuckDB. Deliberately lightweight: no model retraining, no
fine-tuning - just damped movement of the four combination weights, clamped
so no amount of feedback can silence a signal entirely.

Update rule (transparent to judges):
    w_k <- clamp(w_k + delta_k * WEIGHT_STEP, WEIGHT_BOUNDS)
    then renormalize all four weights to sum to 1.0.

Method type logged to telemetry: business_rules.
"""

from datetime import datetime, timezone

from app.config import DEFAULT_CONFIDENCE_WEIGHTS, WEIGHT_BOUNDS, WEIGHT_STEP
from app.services.store import get_conn

# How each feedback action nudges which weight (positive = more trust).
ACTION_DELTAS = {
    "accept": {"did_clarity": +1.0, "cross_source_agreement": +0.5},
    "reject": {"did_clarity": -1.0, "cross_source_agreement": -0.5},
    # A correction means the structured signals missed something the human saw,
    # so we trust statistical significance slightly less relative to unstructured.
    "correct": {"cross_source_agreement": +1.0, "statistical_significance": -0.5},
}


def record_feedback(insight_id: str, action: str, corrected_driver: str | None,
                    kpi_id: str | None) -> dict:
    if action not in ACTION_DELTAS:
        raise ValueError(f"action must be one of {sorted(ACTION_DELTAS)}")

    conn = get_conn()
    next_id = (conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] or 0) + 1
    conn.execute(
        "INSERT INTO feedback VALUES (?, now(), ?, ?, ?, ?)",
        [next_id, insight_id, action, corrected_driver, kpi_id],
    )

    rows = conn.execute("SELECT weight_key, value FROM confidence_weights").fetchall()
    old = {k: float(v) for k, v in rows} or dict(DEFAULT_CONFIDENCE_WEIGHTS)

    deltas = ACTION_DELTAS[action]
    new = {}
    lo, hi = WEIGHT_BOUNDS
    for k, v in old.items():
        candidate = v + deltas.get(k, 0.0) * WEIGHT_STEP
        new[k] = min(hi, max(lo, candidate))
    # renormalize to sum exactly 1.0 (no rounding - callers format for display)
    total = sum(new.values())
    new = {k: v / total for k, v in new.items()}
    for k, v in new.items():
        conn.execute(
            "INSERT OR REPLACE INTO confidence_weights VALUES (?, ?, now())",
            [k, v],
        )

    return {
        "feedback_id": next_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "weights_before": {k: round(v, 3) for k, v in old.items()},
        "weights_after": new,
        "message": "Confidence weights adjusted",
    }


def correction_counts() -> dict:
    """How often humans corrected the engine toward each driver.
    Feeds the damped ranking tiebreak in action_engine and the UI chips."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT corrected_driver, COUNT(*) AS n FROM feedback
           WHERE action = 'correct' AND corrected_driver IS NOT NULL
           GROUP BY corrected_driver ORDER BY n DESC"""
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def feedback_history(limit: int = 50) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT feedback_id, ts, insight_id, action, corrected_driver, kpi_id
           FROM feedback ORDER BY feedback_id DESC LIMIT ?""",
        [limit],
    ).fetchall()
    return [
        {"feedback_id": r[0], "ts": str(r[1]), "insight_id": r[2],
         "action": r[3], "corrected_driver": r[4], "kpi_id": r[5]}
        for r in rows
    ]
