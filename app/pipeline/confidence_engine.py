"""Pipeline stage 6 - confidence_engine.

Combines the deterministic signals into one composite score in [0, 1]:

    score = w1*statistical_significance + w2*did_clarity
          + w3*cross_source_agreement + w4*freshness

Weights are read LIVE from the confidence_weights table (feedback_engine
nudges them; defaults come from config). Tier mapping:

    >= 0.85  Observed              >= 0.75  Strongly Supported
    >= 0.60  Likely                >= 0.45  Possible
    <  0.45  Insufficient Evidence

Contradictory OVERRIDES the numeric tiers: structured data shows no material
move while unstructured evidence screams the opposite (or vice versa).

Abstention: if score < contract.confidence_policy.abstain_below OR tier is
Contradictory -> abstain=true and a DETERMINISTIC clarifying question is
produced. The LLM is never asked to explain around missing evidence.

Method type logged to telemetry: scoring_rules.
"""

import numpy as np

from app.config import DEFAULT_CONFIDENCE_WEIGHTS, WEIGHT_BOUNDS, WEIGHT_STEP
from app.services.store import get_conn

TIERS = [
    (0.85, "Observed"),
    (0.75, "Strongly Supported"),
    (0.60, "Likely"),
    (0.45, "Possible"),
    (0.00, "Insufficient Evidence"),
]


def load_live_weights() -> dict:
    """Current weights from DuckDB (seeded with defaults on first call)."""
    conn = get_conn()
    rows = conn.execute("SELECT weight_key, value FROM confidence_weights").fetchall()
    if not rows:
        for k, v in DEFAULT_CONFIDENCE_WEIGHTS.items():
            conn.execute(
                "INSERT OR REPLACE INTO confidence_weights VALUES (?, ?, now())", [k, v]
            )
        rows = [(k, v) for k, v in DEFAULT_CONFIDENCE_WEIGHTS.items()]
    return {k: float(v) for k, v in rows}


def _stat_score(z: float, threshold_z: float) -> float:
    return float(min(1.0, abs(z) / (2.0 * threshold_z))) if z else 0.0


def sentiment_signal(tickets_df, region: str | None):
    """Latest-month regional sentiment + its deviation from that region's norm."""
    t = tickets_df.copy()
    if region:
        t = t[t["region"] == region]
    months = sorted(t["month"].astype(str).unique())
    latest = months[-1]
    cur = t[t["month"].astype(str) == latest]["avg_sentiment_score"].mean()
    hist = t[t["month"].astype(str) < latest]["avg_sentiment_score"]
    mu = float(hist.mean()) if len(hist) else 0.25
    sd = float(hist.std(ddof=0)) or 0.15
    z = (cur - mu) / sd
    return {"latest_month": latest, "avg_sentiment": round(float(cur), 3),
            "historical_mean": round(mu, 3), "z": round(float(z), 2)}


LOWER_IS_BETTER = {"return_rate", "customer_acquisition_cost"}


def cross_source_agreement(detection, sent_z: float, kpi_id: str = "") -> tuple[float, bool]:
    """Agreement in [0,1] between structured movement and unstructured signal.

    Direction-aware: 'bad' depends on the KPI. Rising return_rate is bad;
    falling net_revenue is bad. Bad-structured + bad-unstructured AGREE even
    when both raw signs differ from the revenue case.

    Returns (agreement, contradictory_flag).
    """
    lower_better = kpi_id in LOWER_IS_BETTER
    structured_bad = detection.material and (
        (detection.direction == "up") == lower_better)
    structured_good = detection.material and not structured_bad
    neg_sentiment = sent_z <= -1.5
    pos_sentiment = sent_z >= 1.5

    if not detection.material:
        if neg_sentiment:
            return 0.10, True     # hidden-risk contradiction
        return 0.55, False        # nothing happening anywhere - mildly informative

    if structured_bad and neg_sentiment:
        return 1.0, False
    if structured_good and pos_sentiment:
        return 1.0, False
    if structured_good and neg_sentiment:
        return 0.05, True         # e.g. margin up while customers complain
    if structured_bad and pos_sentiment:
        return 0.05, True         # e.g. returns up while CSAT looks fine
    return 0.7, False             # neutral unstructured signal


def _clarifying_question(kpi_name: str, scope_label: str, sent,
                         direction: str | None = None) -> str:
    if direction == "up":
        stem = (
            f"{kpi_name} in {scope_label} improved ({direction}ward), yet support-ticket "
            f"sentiment turned sharply negative (avg {sent['avg_sentiment']:+.2f} vs typical "
            f"{sent['historical_mean']:+.2f} in {sent['latest_month']}). "
        )
        ask = (
            "Before this improvement is acted on: has a pricing, fulfillment or product "
            "issue been reported by the "
            f"{scope_label} team that the current margin gain does not reflect? "
            "Confirm and I will re-run the analysis treating it as a candidate driver."
        )
    else:
        stem = (
            f"Support-ticket sentiment in {scope_label} turned sharply negative "
            f"(avg {sent['avg_sentiment']:+.2f} vs typical {sent['historical_mean']:+.2f} "
            f"in {sent['latest_month']}), but {kpi_name} shows no material movement yet. "
        )
        ask = (
            "Has an emerging fulfillment, quality or carrier issue been reported by the "
            f"{scope_label} team that has not reached order data? Confirm and I will "
            "re-run the analysis treating it as a candidate driver."
        )
    return stem + ask


def compute_confidence(contract, detection, did_results: list,
                       freshness_factor: float, sent_z: float,
                       weights: dict | None = None, scope_label: str = "") -> dict:
    w = weights or load_live_weights()

    stat = _stat_score(detection.z_score, contract.statistical_threshold_zscore)

    # DiD context isolation: only tests that evaluate THIS KPI's metric and
    # region count toward causal corroboration, and only if they are strictly
    # supported (p<0.05 AND parallel-trends ok). Cross-region / cross-metric
    # tests (e.g. a West return-rate test on a North revenue card) are ignored.
    from app.pipeline.causal_engine import did_relevant, did_expects_did, did_supported
    region = None if scope_label == "ALL_REGIONS" else scope_label
    relevant = [r for r in (did_results or []) if did_relevant(r, contract.kpi_id, region)]
    proof = [r for r in relevant if did_supported(r)]
    had_any_did = did_expects_did(contract.kpi_id)

    relevant_clarities = [r.clarity for r in proof]
    did_component = max(relevant_clarities) if relevant_clarities else 0.0

    agreement, contradictory = cross_source_agreement(detection, sent_z,
                                                      kpi_id=contract.kpi_id)

    keys = ["statistical_significance", "did_clarity", "cross_source_agreement", "freshness"]
    vals = {"statistical_significance": stat, "did_clarity": did_component,
            "cross_source_agreement": agreement, "freshness": freshness_factor}
    total_w = sum(w.get(k, 0.0) for k in keys) or 1.0
    score = sum(w.get(k, 0.0) * vals[k] for k in keys) / total_w

    # Strict abstention override: if this KPI relies on DiD causality but there
    # is NO statistically supported, regionally-relevant DiD test, we MUST NOT
    # green-light a driver (e.g. an invented "Competitor Activity"). Force the
    # composite score below the contract's abstention floor so the engine emits
    # ANALYSIS WITHHELD / UNMODELED FACTOR and asks for clarification instead of
    # recommending a lever into a cause we cannot prove.
    no_proof = had_any_did and not proof
    if no_proof:
        score = max(0.0, min(score, contract.abstain_below - 0.001))

    tier = next(name for floor, name in TIERS if score >= floor)
    if contradictory:
        tier = "Contradictory"

    abstain = contradictory or score < contract.abstain_below

    out = {
        "score": round(float(score), 3),
        "tier": tier,
        "abstain": bool(abstain),
        "components": {
            "statistical_significance": round(stat, 3),
            "did_clarity": round(did_component, 3),
            "cross_source_agreement": round(agreement, 3),
            "freshness": round(float(freshness_factor), 3),
        },
        "weights_used": {k: round(float(w.get(k, 0.0)), 3) for k in keys},
        "contradictory": bool(contradictory),
        "sentiment_context": None,
    }
    if contradictory:
        from app.services import store as _store  # local import avoids cycle at module load
        sent = sentiment_signal(_store.get_tickets(), scope_label if scope_label != "ALL_REGIONS" else None)
        out["sentiment_context"] = sent
        out["clarifying_question"] = _clarifying_question(
            contract.name, scope_label or "this region", sent,
            detection.direction if detection.material else None,
        )
    return out
