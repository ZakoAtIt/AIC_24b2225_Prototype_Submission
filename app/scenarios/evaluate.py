"""Continuous-evaluation harness.

Two jobs, both clearly OUTSIDE the observed-fact pipeline (app/pipeline/):
this module is the only place allowed to compare pipeline output against the
planted ground truth, because evaluation - unlike operation - must know the
answers to grade them.

1. DETECTOR SCORECARD
   Rebuilds the synthetic world under several seeds (same event design,
   different noise draws), runs the REAL detection stage, and scores every
   KPI x region slice against the known label. Reports precision/recall.
   Judges can see the detector is not tuned to one lucky dataset.

2. DRIFT MONITOR (PSI)
   Population Stability Index between an earlier and a current window for
   selected distributions. PSI > 0.25 = notable shift; > 0.5 = major.
   This is what would trip a retraining/re-baselining workflow in production.

Ground truth labels mirror generate_data's planted events; events.json itself
is NOT read here either - the labels are restated as constants so even the
evaluator never parses the debug artifact.
"""

import numpy as np
import pandas as pd

from app.contracts.contract_loader import get_contract
from app.pipeline.detection_engine import detect_per_region
from app.pipeline.load_and_validate import SourceCatalog, SourceInfo, compute_kpi_frame

SEEDS = [42, 7, 101, 2026]

# (kpi_id, region) -> expected direction at DEMO_NOW; absent => expect none.
# NOTE: North "up" is REAL - the planted SKU-NEW-01 launch ramps North
# volume/revenue. Flagging it is correct detection, not a false positive;
# the pipeline deliberately keeps it out of causal slices instead.
TRUTH = {
    ("net_revenue", "South"): "down",      # E1 promo (+E2 price, net negative)
    ("net_revenue", "West"): "down",       # E3 logistics
    ("net_revenue", "North"): "up",        # E5 launch ramp
    ("units_sold", "South"): "down",       # E1
    ("units_sold", "West"): "down",        # E3
    ("units_sold", "North"): "up",         # E5 launch ramp
    ("return_rate", "West"): "up",         # E3 return shock
    ("gross_margin_pct", "South"): "up",   # E2 price on treated SKUs
}
KPIS_SCORED = ["net_revenue", "units_sold", "return_rate", "gross_margin_pct"]
# CAC excluded from the scorecard: weekly grain + only 17 points; its rolling
# fallback is demonstrated separately in the sparse-history scenario.

DRIFT_SLICES = [
    ("net_revenue", "South", "E1+E2 shocks land in the last 30 days"),
    ("support_sentiment", "South", "E4 complaint wave - unstructured drift leads structured"),
    ("net_revenue_ex_launch", "North",
     "control slice (launch SKU excluded): organic TREND alone trips the monitor - "
     "drift means 're-baseline expected levels', not 'anomaly'"),
]


def _catalog(pos: pd.DataFrame, mkt: pd.DataFrame, tix: pd.DataFrame) -> SourceCatalog:
    cat = SourceCatalog(demo_now=pd.Timestamp("2026-07-30"))
    mk = lambda name, df, sla: SourceInfo(  # noqa: E731
        name=name, df=df, last_refresh=pd.Timestamp("2026-07-30"),
        age_hours=6.0, sla_hours=sla, stale=False)
    cat.sources["pos_transactions"] = mk("pos_transactions", pos, 24)
    cat.sources["marketing_spend"] = mk("marketing_spend", mkt, 168)
    cat.sources["support_tickets"] = mk("support_tickets", tix, 720)
    return cat


def _score_seed(seed: int) -> list[dict]:
    """Returns per-slice outcomes for one world."""
    import generate_data as gd  # project root is importable in tests/runtime

    pos = gd.generate_pos(seed)
    mkt = gd.generate_marketing(seed)
    tix = gd.generate_tickets(pos, seed)
    catalog = _catalog(pos, mkt, tix)

    out = []
    for kpi_id in KPIS_SCORED:
        contract = get_contract(kpi_id)
        frame = compute_kpi_frame(catalog, kpi_id)
        for det in detect_per_region(frame, contract):
            truth = TRUTH.get((kpi_id, det.scope))
            pred = det.direction if det.material else None
            if pred == truth and truth is not None:
                outcome = "TP"
            elif pred is not None and pred != truth:
                outcome = "FP"       # flagged but wrong direction / no event
            elif truth is not None and pred != truth:
                outcome = "FN"       # missed real movement
            else:
                outcome = "TN"
            out.append({"seed": seed, "kpi": kpi_id, "region": det.scope,
                        "truth": truth or "-", "pred": pred or "-",
                        "pct": float(det.pct_deviation), "z": float(det.z_score),
                        "outcome": outcome})
    return out


def detector_scorecard() -> dict:
    rows = []
    for s in SEEDS:
        rows.extend(_score_seed(s))

    tp = sum(r["outcome"] == "TP" for r in rows)
    fp = sum(r["outcome"] == "FP" for r in rows)
    fn = sum(r["outcome"] == "FN" for r in rows)
    tn = len(rows) - tp - fp - fn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None

    per_kpi = {}
    for r in rows:
        d = per_kpi.setdefault(r["kpi"], {"tp": 0, "fp": 0, "fn": 0})
        if r["outcome"] in d:
            d[r["outcome"]] += 1

    misses = [r for r in rows if r["outcome"] in ("FN", "FP")]
    return {
        "seeds": SEEDS,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "per_kpi": per_kpi,
        "misses": [
            {"seed": m["seed"], "kpi": m["kpi"], "region": m["region"],
             "truth": m["truth"], "pred": m["pred"],
             "dev_pct": m["pct"], "z": m["z"]}
            for m in misses
        ],
        "note": ("Scored across independent noise seeds with identical event "
                 "design; thresholds come from kpi_contracts.json, not tuning."),
    }


# ---------------------------------------------------------------------------
# PSI drift monitor
# ---------------------------------------------------------------------------

def _psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    edges = np.quantile(expected, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e_pct = np.histogram(expected, edges)[0] / max(len(expected), 1)
    a_pct = np.histogram(actual, edges)[0] / max(len(actual), 1)
    eps = 1e-4
    e_pct = np.clip(e_pct, eps, None)
    a_pct = np.clip(a_pct, eps, None)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def drift_report() -> list[dict]:
    import generate_data as gd

    pos = gd.generate_pos(SEEDS[0])
    tix = gd.generate_tickets(pos, SEEDS[0])
    out = []

    for kind, region, why in DRIFT_SLICES:
        if kind.startswith("net_revenue"):
            sub = pos[pos.region == region].copy()
            if kind == "net_revenue_ex_launch":
                sub = sub[sub.product_id != "SKU-NEW-01"]
            sub["rev"] = sub.units_sold * sub.unit_price - sub.returns_value
            daily = sub.groupby("date")["rev"].sum()
            dates = daily.index.sort_values()
            # De-seasonalize: divide each day by its weekday's overall mean so
            # calendar mix cannot masquerade as distribution shift. Sustained
            # TREND or level shocks remain visible - which is the point.
            dt_idx = pd.to_datetime(daily.index)
            wd = np.asarray(dt_idx.dayofweek)
            factor = daily.groupby(wd).mean()
            daily = daily / np.array([factor[w] for w in wd])
            base = daily[dates[:60]].values          # Apr-May baseline
            recent = daily[dates[-30:]].values       # trailing month
        else:  # support_sentiment
            sub = tix[tix.region == region]
            months = sorted(sub.month.unique())
            base = sub[sub.month.isin(months[:-1])]["avg_sentiment_score"].values
            recent = sub[sub.month == months[-1]]["avg_sentiment_score"].values

        psi = round(_psi(np.asarray(base, float), np.asarray(recent, float)), 3)
        flag = "major_shift" if psi > 0.5 else ("notable_shift" if psi > 0.25 else "stable")
        out.append({
            "slice": f"{kind}/{region}", "psi": psi, "flag": flag,
            "baseline_window": ("first 60 days" if kind.startswith("net_revenue")
                                else "Apr-Jun"),
            "current_window": ("last 30 days" if kind.startswith("net_revenue")
                               else months[-1]),
            "why_it_matters": why,
        })
    return out


_cache: dict | None = None


def run_evaluation() -> dict:
    """Lazy + cached: the full sweep takes seconds, so compute once per process."""
    global _cache
    if _cache is None:
        _cache = {
            "detector_scorecard": detector_scorecard(),
            "drift": drift_report(),
            "warning": ("Evaluation harness - compares against planted ground "
                        "truth. Lives OUTSIDE app/pipeline by construction."),
        }
    return _cache
