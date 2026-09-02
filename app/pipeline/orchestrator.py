"""Pipeline orchestrator: runs stages 1-9 in order for one analysis request.

This is the ONLY module that knows the whole sequence. Each stage is timed
and tagged with its method type; every quantitative output is registered as
evidence. The facts bundle it returns is the single source both personas
render from.

RBAC nuance (documented deliberately): causal identification requires
control regions. Analysts therefore receive DiD EFFECTS but control-region
raw deltas are redacted - row-level security protects raw cross-region rows
while preserving the analytical conclusion.
"""

import time

import pandas as pd
from scipy.stats import norm as _norm

from app.contracts.contract_loader import get_contract
from app.pipeline import confidence_engine, detection_engine
from app.pipeline.action_engine import generate_actions
from app.pipeline.causal_engine import run_cac_did, run_pos_did_suite
from app.pipeline.decomposition_engine import waterfall_net_revenue
from app.pipeline.evidence_registry import EvidenceRegistry
from app.pipeline.load_and_validate import compute_kpi_frame, load_and_validate
from app.pipeline.llm_narrative import build_payload, generate_narrative
from app.pipeline.persona_router import render
from app.pipeline.semantic_resolver import resolve_access
from app.services import telemetry

def _timed(name, method_type, fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    telemetry.record_stage(name, method_type, (time.perf_counter() - t0) * 1000)
    return result


def run_overview() -> list[dict]:
    """GET /kpis - severity-ranked card data for all five KPIs.

    Deliberately UNCACHED: the source CSVs can be mutated at any moment during
    chaos testing, so every call re-loads and re-computes from the freshest
    data rather than serving a stale 60s snapshot.
    """
    catalog = _timed("load_and_validate", "data_engineering", load_and_validate)
    lower_is_better = {"return_rate", "customer_acquisition_cost"}
    cards = []
    for kpi_id in ["net_revenue", "gross_margin_pct", "units_sold",
                   "return_rate", "customer_acquisition_cost"]:
        contract = get_contract(kpi_id)
        frame = _timed(f"compute::{kpi_id}", "data_engineering",
                       compute_kpi_frame, catalog, kpi_id)
        dets = _timed(f"detect::{kpi_id}", "statistics",
                      detection_engine.detect_per_region, frame, contract)
        material_dets = [d for d in dets if d.material]
        if material_dets:
            worst = max(material_dets,
                        key=lambda d: abs(d.pct_deviation) * abs(d.z_score))
            # adverse if the movement direction is the 'bad' one for this KPI
            adverse = (worst.direction == "up") == (kpi_id in lower_is_better)
            status = "adverse" if adverse else "favorable"
            severity = round(abs(worst.pct_deviation) * abs(worst.z_score), 1)
        else:
            worst = max(dets, key=lambda d: abs(d.pct_deviation)) if dets else None
            # near-threshold watch: real deviation, gates not both passed yet
            watch = bool(worst and abs(worst.z_score) >=
                         0.5 * contract.statistical_threshold_zscore)
            status = "watch" if watch else "normal"
            severity = round(abs(worst.pct_deviation) * abs(worst.z_score), 1) if worst else 0.0
        cards.append({
            "kpi_id": kpi_id,
            "name": contract.name,
            "unit": contract.unit,
            "owner": contract.owner,
            "status": status,
            "severity": severity,
            "material": bool(material_dets),
            "scope": worst.scope if worst else "-",
            "pct_deviation": worst.pct_deviation if worst else 0.0,
            "z_score": worst.z_score if worst else 0.0,
            "direction": worst.direction if worst else "none",
            "method_used": worst.method_used if worst else "-",
            "thresholds": {
                "materiality_pct": contract.materiality_threshold_pct,
                "zscore": contract.statistical_threshold_zscore,
            },
        })
    cards.sort(key=lambda c: (-c["severity"], c["kpi_id"]))
    return cards


def run_analysis(kpi_id: str, role: str, user_region: str | None,
                 focus_region: str | None, persona: str) -> dict:
    registry = EvidenceRegistry()
    contract = get_contract(kpi_id)

    # Stage 1 ------------------------------------------------------------------
    catalog = _timed("load_and_validate", "data_engineering", load_and_validate)
    pos_info = catalog.get("pos_transactions")

    # Stage 2 ------------------------------------------------------------------
    scope = _timed("semantic_resolver", "business_rules",
                   resolve_access, catalog, kpi_id, role, user_region, focus_region)

    # Stage 3 - detection on the focused slice ---------------------------------
    det = _timed("detection_engine", "statistics",
                 detection_engine.detect_movement, scope.kpi_frame, contract,
                 scope.focus_label)

    freshness = min(catalog.get(s).freshness_factor() for s in contract.sources)
    reg = registry.add(
        claim=f"{contract.name} deviation vs expected baseline ({det.direction}) in {scope.focus_label}",
        value=det.pct_deviation, unit="percent", source=contract.sources[0],
        method=det.method_used, method_type="statistics",
        freshness={"age_hours": pos_info.age_hours if pos_info else None,
                   "sla_hours": contract.freshness_sla_hours},
        lineage=contract.lineage,
        extra={"z_score": det.z_score, "thresholds": {
            "z": contract.statistical_threshold_zscore,
            "materiality_pct": contract.materiality_threshold_pct}},
    )
    z_ev = registry.add(claim=f"{contract.name} paired-weekday z-score",
                        value=det.z_score, unit="z", source=contract.sources[0],
                        method=det.method_used, method_type="statistics",
                        lineage=contract.lineage)
    if det.z_score is not None and abs(det.z_score) > 0.0:
        p_val = float(2 * _norm.sf(abs(det.z_score)))
        registry.add(claim=f"{contract.name} two-tailed p-value (scipy.stats)",
                     value=p_val, unit="p_value", source=contract.sources[0],
                     method=det.method_used, method_type="statistics",
                     lineage=[f"derived from z-score {det.z_score}"],
                     extra={"z_score": det.z_score})

    # Stage 4 - exact waterfall (only where the contract allows it) -------------
    waterfall = None
    if contract.method_allowed("waterfall_decomposition"):
        waterfall = _timed("decomposition_engine", "deterministic_arithmetic",
                           waterfall_net_revenue, scope.pos_rows,
                           None if scope.focus_label == "ALL_REGIONS" else scope.focus_label)
        for comp in waterfall.components_usd_per_day:
            ev_id = registry.add(
                claim=f"Driver '{comp['driver']}' contribution to {contract.name} movement",
                value=comp["effect"], unit="USD/day", source="pos_transactions",
                method="waterfall_decomposition", method_type="deterministic_arithmetic",
                lineage=["pos_transactions.raw -> window_comparison -> price_volume_mix_returns"],
                extra={"pct_of_movement": comp["pct_of_movement"]},
            )
            registry.add(
                claim=f"Driver '{comp['driver']}' share of total movement",
                value=comp["pct_of_movement"], unit="percent_of_movement",
                source="pos_transactions",
                method="waterfall_decomposition", method_type="deterministic_arithmetic",
                lineage=[f"derived from evidence for '{comp['driver']}' contribution"],
            )

    # Stage 5 - layered DiD ------------------------------------------------------
    did_results = []
    if contract.method_allowed("DiD_causal_test"):
        if kpi_id == "customer_acquisition_cost":
            cac = _timed("causal_engine::cac", "causal_inference",
                         run_cac_did, catalog.get("marketing_spend").df)
            if cac:
                did_results = [cac]
        else:
            did_results = _timed("causal_engine", "causal_inference",
                                 run_pos_did_suite, catalog.get("pos_transactions").df)
        for r in did_results:
            did_ev = registry.add(
                claim=f"DiD {r.test_id}: {r.hypothesis}",
                value=r.did_effect,
                unit=("USD/day" if r.test_id[0] in "ABC" else
                      ("pp" if r.test_id.startswith("D") else "USD/customer")),
                source="pos_transactions" if not r.test_id.startswith("E") else "marketing_spend",
                method="difference_in_differences", method_type="causal_inference",
                lineage=["treatment_pre_post -> minus -> control_pre_post"],
                extra={"controls": r.controls, "clarity": r.clarity},
            )
            r.evidence_id = did_ev
            if r.did_effect_pct is not None:
                registry.add(
                    claim=f"DiD {r.test_id}: effect as percent of treatment base",
                    value=r.did_effect_pct, unit="percent", source="pipeline",
                    method="difference_in_differences", method_type="causal_inference",
                    lineage=[f"derived from DiD {r.test_id} effect"],
                )
            if r.did_p_value is not None and r.did_ci_lo is not None:
                registry.add(
                    claim=f"DiD {r.test_id}: statistical significance",
                    value=r.did_p_value, unit="p-value", source="pipeline",
                    method="difference_in_differences", method_type="causal_inference",
                    lineage=["robust OLS (HC1) on post x treated interaction"],
                    extra={"did_se": r.did_se, "ci_lo": r.did_ci_lo, "ci_hi": r.did_ci_hi,
                           "did_effect": r.did_effect},
                )
            if r.parallel_trends != "n/a":
                registry.add(
                    claim=f"DiD {r.test_id}: parallel-trends check = {r.parallel_trends}",
                    value=1.0 if r.parallel_trends == "pass" else
                          (0.5 if r.parallel_trends == "caution" else 0.0),
                    unit="flag", source="pipeline",
                    method="placebo_pre_period_t_test", method_type="causal_inference",
                    lineage=["pre-period treated-minus-control differential t-test"],
                    extra={"parallel_trends": r.parallel_trends,
                           "parallel_trends_p": r.parallel_trends_p},
                )

    # Analyst RBAC: redact raw control-region numbers, keep conclusions.
    visible_dids = did_results
    if role == "analyst":
        from app.pipeline.causal_engine import DiDResult
        visible_dids = [
            DiDResult(r.test_id, r.hypothesis, r.treatment, ["[restricted]"],
                      r.pre_window, r.post_window, r.treat_delta, [],
                      r.did_effect, r.did_effect_pct, r.clarity, r.verdict,
                      r.did_se, r.did_p_value, r.did_ci_lo, r.did_ci_hi,
                      r.parallel_trends, r.parallel_trends_p, r.evidence_id)
            for r in did_results
        ]

    # Stage 6 - confidence -------------------------------------------------------
    sent = confidence_engine.sentiment_signal(catalog.get("support_tickets").df,
                                              None if scope.focus_label == "ALL_REGIONS"
                                              else scope.focus_label)
    conf = _timed("confidence_engine", "scoring_rules",
                  confidence_engine.compute_confidence,
                  contract, det, did_results, freshness, sent["z"],
                  confidence_engine.load_live_weights(), scope.focus_label)
    score_ev = registry.add(claim=f"Composite confidence for {contract.kpi_id}",
                            value=conf["score"], unit="score", source="pipeline",
                            method="weighted_composite", method_type="scoring_rules")
    if sent.get("z") is not None and scope.focus_label != "ALL_REGIONS":
        registry.add(claim=f"Ticket sentiment z-score drift ({scope.focus_label})",
                     value=sent["z"], unit="z", source="support_tickets",
                     method="sentiment_aggregation", method_type="retrieval",
                     lineage=["support_tickets.monthly_avg -> zscore_vs_prior"],
                     extra={"latest_month": str(sent.get("latest_month", "")),
                            "threshold_z": 0.25})

    # Ticket snippet retrieval (simple keyword match - the ONLY retrieval step)
    snippet = _timed("ticket_retrieval", "retrieval", _top_ticket_snippet,
                     catalog.get("support_tickets").df, scope.focus_label, sent)

    # Stage 7 - actions ------------------------------------------------------------
    from app.services.feedback_engine import correction_counts
    actions = _timed("action_engine", "business_rules", generate_actions,
                     contract, waterfall, did_results, conf["tier"], scope.focus_label,
                     correction_counts())
    for a in actions:
        registry.add(claim=f"Expected impact of recommended action ({a.driver})",
                     value=a.expected_impact_value, unit=a.impact_unit,
                     source="rule_table", method="contribution_reversal",
                     method_type="business_rules", lineage=["waterfall -> reversal"])

    # Stage 6b - cross-source marketing context -----------------------------------
    marketing_context_items: list[dict] = []
    if scope.focus_label and scope.focus_label != "ALL_REGIONS":
        mkt_df = catalog.get("marketing_spend").df
        try:
            region_mkt = mkt_df[mkt_df["region"] == scope.focus_label].copy()
            if len(region_mkt) and "spend_usd" in region_mkt.columns:
                region_mkt["week_start"] = pd.to_datetime(region_mkt["week_start"])
                weekly = region_mkt.groupby(["week_start", "channel"])["spend_usd"].sum().reset_index()
                for ch in weekly["channel"].unique():
                    ch_df = weekly[weekly["channel"] == ch].sort_values("week_start")
                    if len(ch_df) >= 6:
                        baseline = ch_df["spend_usd"].iloc[:-4].mean()
                        recent = ch_df["spend_usd"].iloc[-4:].mean()
                        if baseline > 0:
                            multiple = round(recent / baseline, 2)
                            if multiple > 1.5 or multiple < 0.5:
                                ev_id = registry.add(
                                    claim=f"Marketing spend context ({scope.focus_label}): {ch} spend ran {multiple}x its trailing-4-week baseline",
                                    value=multiple, unit="x_baseline",
                                    source="marketing_spend",
                                    method="trend_comparison",
                                    method_type="business_rules",
                                    lineage=["marketing_spend.weekly_agg -> baseline_ratio"],
                                    extra={"channel": ch,
                                           "baseline_weeks": len(ch_df) - 4,
                                           "recent_weeks": 4})
                                marketing_context_items.append({
                                    "source": "marketing_spend",
                                    "claim": f"{ch} spend ran {multiple}x baseline in {scope.focus_label}",
                                    "value": multiple,
                                    "unit": "x_baseline",
                                    "evidence_id": ev_id,
                                })
        except Exception:
            pass

    # Alternative hypotheses: every test that was weak/inconclusive/rejected,
    # surfaced for the Analyst to see what was considered and why it was set aside.
    alt_hypotheses: list[dict] = []
    for r in did_results:
        if r.verdict != "supported":
            reason = (
                "control-region clarity insufficient" if r.clarity and r.clarity < 0.5
                else "window too short or no significant treatment-control divergence"
            )
            alt_hypotheses.append({
                "test_id": r.test_id,
                "hypothesis": r.hypothesis,
                "verdict": r.verdict,
                "why_set_aside": reason,
            })

    # Stage 8+9 - narrative + persona -------------------------------------------------
    movement = {
        "kpi_id": contract.kpi_id, "name": contract.name, "unit": contract.unit,
        "scope": scope.focus_label,
        "material": det.material, "direction": det.direction,
        "pct_deviation": det.pct_deviation, "z_score": det.z_score,
        "method_used": det.method_used,
        "eval_window_days": det.eval_window_days,
        "total_delta_per_day": getattr(waterfall, "total_delta_per_day", None),
        "identity_check_passed": getattr(waterfall, "identity_check_passed", None),
    }
    drivers = []
    if waterfall is not None:
        driver_evs = {}
        for item in registry.items:
            if item.get("method") == "waterfall_decomposition":
                name = item["claim"].split("'")[1]
                driver_evs[name] = item["evidence_id"]
        for c in waterfall.components_usd_per_day:
            drivers.append({
                "driver": c["driver"], "effect": c["effect"], "unit": "USD/day",
                "pct_of_movement": c["pct_of_movement"],
                "evidence_id": driver_evs.get(c["driver"]),
            })
    elif did_results:
        # Fallback top-driver: use ONLY a DiD test that is regionally+KPI-relevant
        # AND strictly supported for THIS card. Never fabricate a driver (e.g.
        # "Competitor Activity") from a non-relevant or non-significant test.
        from app.pipeline.causal_engine import did_relevant, did_supported
        region = None if scope.focus_label == "ALL_REGIONS" else scope.focus_label
        admissible = [r for r in did_results
                      if did_relevant(r, contract.kpi_id, region) and did_supported(r)]
        if admissible:
            top = max(admissible, key=lambda r: abs(r.did_effect))
            drivers.append({
                "driver": "marketing_spend_efficiency" if top.test_id.startswith("E") else top.hypothesis,
                "effect": top.did_effect, "unit": "USD/customer" if top.test_id.startswith("E") else "pp",
                "pct_of_movement": top.did_effect_pct, "evidence_id": top.evidence_id,
            })

    payload = _timed("payload_assembly", "business_rules", build_payload,
                     {"kpi_id": contract.kpi_id, "name": contract.name,
                      "unit": contract.unit, "owner": contract.owner,
                      "abstain_below": contract.abstain_below,
                      "materiality_threshold_pct": contract.materiality_threshold_pct},
                     movement, drivers, visible_dids, actions, conf, evidence=registry,
                     persona=persona)

    # Cross-source context: give the narrator genuinely heterogeneous evidence
    # so the story weaves POS, marketing, and ticket data together, not just POS.
    cross_source: list[dict] = []
    # Always include sentiment snippet if available
    if snippet:
        sent_ev = {
            "source": "support_tickets",
            "claim": f"Unstructured signal ({scope.focus_label}): sentiment score {sent['z']:.2f}z",
            "value": round(sent["z"], 2),
            "unit": "z",
            "evidence_id": None,
            "snippet": snippet.get("text", "")[:120] if snippet else None,
        }
        # Find the ticket sentiment evidence ID if registered
        for ev in registry.items:
            if ev.get("source") == "support_tickets" and "sentiment" in ev.get("claim", ""):
                sent_ev["evidence_id"] = ev["evidence_id"]
                break
        cross_source.append(sent_ev)
    # Add marketing context items
    cross_source.extend(marketing_context_items)
    if cross_source:
        payload["cross_source_context"] = cross_source

    # Human-in-the-loop context: corrections already integrated via /feedback.
    # Deliberately number-free so the narrator cannot cite unregistered counts.
    from app.services.feedback_engine import correction_counts
    flagged = sorted(correction_counts())
    if flagged:
        payload["analyst_context"] = (
            "Analysts have previously corrected the engine toward these drivers: "
            + ", ".join(flagged) + "."
        )

    narr = _timed("llm_narrative", "LLM", generate_narrative, payload, registry)
    telemetry.record_llm(narr["usage"]["prompt_tokens"], narr["usage"]["completion_tokens"],
                         narr["cost_usd"], narr["cache_hit"])

    facts = {
        "insight_id": f"{contract.kpi_id}:{scope.focus_label}:{persona}",
        "kpi": {"kpi_id": contract.kpi_id, "name": contract.name, "unit": contract.unit,
                "owner": contract.owner, "abstain_below": contract.abstain_below,
                "formula": contract.formula,
                "materiality_threshold_pct": contract.materiality_threshold_pct,
                "statistical_threshold_zscore": contract.statistical_threshold_zscore},
        "movement": movement,
        "series": det.series,
        "drivers": drivers,
        "waterfall_identity_passed": getattr(waterfall, "identity_check_passed", None),
        "causal_results": visible_dids,
        "actions": actions,
        "confidence": conf,
        "sentiment_context": sent | {"snippet": snippet},
        "evidence": registry.items,
        "freshness": catalog.freshness_payload(),
        "lineage": contract.lineage,
        "restricted_columns": scope.restricted_columns,
        "demo_now": str(catalog.demo_now),
        "cross_source_context": cross_source,
        "alternative_hypotheses": alt_hypotheses,
    }

    view = _timed("persona_router", "business_rules", render, facts, narr, persona)

    # Audit trail: persist this request's full evidence registry so any
    # insight can be re-examined after the fact (GET /audit/{request_id}).
    st = telemetry.current()
    if st is not None:
        telemetry.log_evidence(st.request_id, contract.kpi_id, registry.items)

    return {"facts": facts, "view": view, "narrative_meta": {
        "validated": narr["validated"], "violations": narr["violations"],
        "cache_hit": narr["cache_hit"], "model": narr["model"],
        "cost_usd": narr["cost_usd"],
    }, "registry_payload": registry.as_payload(), "_catalog": catalog}


def _top_ticket_snippet(tix_df, region_label, sent):
    """Simple keyword retrieval of one representative complaint (no embeddings)."""
    try:
        sub = tix_df[tix_df["region"] == region_label] if region_label else tix_df
        month = sent.get("latest_month")
        sub = sub[sub["month"].astype(str) == str(month)]
        sub = sub.sort_values("avg_sentiment_score")
        if len(sub):
            r = sub.iloc[0]
            return {"product_id": r["product_id"], "sentiment": float(r["avg_sentiment_score"]),
                    "text": r["sample_ticket_text"]}
    except Exception:
        pass
    return None
