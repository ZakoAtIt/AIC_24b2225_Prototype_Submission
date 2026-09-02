"""Pipeline stage 9 - persona_router.

Same facts, two renderings. The CFO view consumes the (validated) LLM
narrative; the Analyst view is rendered DETERMINISTICALLY from the facts
bundle - tables and raw numbers need no language model, which is both
cheaper and more precise. Persona switching therefore never re-runs the
pipeline or re-calls the LLM.

Method type logged to telemetry: business_rules.
"""


def _delivery_channel(persona: str, conf: dict, urgency: str) -> dict:
    """Where this insight should be pushed. Rule-based, documented:
    CFOs get proactive email digests for urgent confirmed movements and are
    otherwise left to the dashboard; analysts work inside the workspace
    (interactive depth, no push noise)."""
    if persona == "cfo":
        if conf.get("abstain"):
            return {"channel": "dashboard",
                    "reason": "Withheld analysis stays in the workspace - nothing urgent to push"}
        if urgency in ("now", "this_week"):
            return {"channel": "email_digest",
                    "reason": f"Material movement with {urgency} urgency - proactive push to CFO digest"}
        return {"channel": "dashboard", "reason": "Within normal band - available on demand"}
    return {"channel": "workspace", "reason": "Analysts receive full interactive depth in-app"}


def render(facts: dict, narrative_result: dict, persona: str) -> dict:
    """facts: the full structured analysis bundle. Returns the persona view."""
    if persona == "cfo":
        return _render_cfo(facts, narrative_result)
    return _render_analyst(facts)


def _render_cfo(facts: dict, narrative_result: dict) -> dict:
    n = narrative_result["narrative"]
    conf = facts["confidence"]
    mov = facts["movement"]
    actions = facts["actions"]
    return {
        "persona": "cfo",
        "headline": n.get("headline", ""),
        "summary": n.get("summary", ""),
        "dollar_impact": {
            "delta_per_day": mov.get("total_delta_per_day"),
            "unit": "USD/day",
            "scope": mov.get("scope"),
        },
        "top_driver": (facts["drivers"][0] if facts["drivers"] else None),
        "recommended_action": (actions[0] if actions else None),
        "urgency": n.get("urgency", "monitor"),
        "confidence_tier": conf["tier"],
        "abstain": conf["abstain"],
        "clarifying_question": conf.get("clarifying_question"),
        "key_numbers": n.get("key_numbers", []),
        "delivery_channel": _delivery_channel("cfo", conf, n.get("urgency", "monitor")),
        "validated_by_pipeline": narrative_result["validated"],
    }


def _render_analyst(facts: dict) -> dict:
    conf = facts["confidence"]
    return {
        "persona": "analyst",
        "kpi": facts["kpi"],
        "movement": facts["movement"],
        "series": facts.get("series"),
        "drivers_ranked": facts["drivers"],
        "waterfall_identity_passed": facts.get("waterfall_identity_passed"),
        "causal_tests": [
            {
                "test_id": r.test_id,
                "hypothesis": r.hypothesis,
                "treatment": r.treatment,
                "controls": r.controls,
                "windows": {"pre": r.pre_window, "post": r.post_window},
                "treat_delta": r.treat_delta,
                "control_deltas": r.ctrl_deltas,
                "did_effect": r.did_effect,
                "did_effect_pct": r.did_effect_pct,
                "did_se": r.did_se,
                "did_p_value": r.did_p_value,
                "did_ci_lo": r.did_ci_lo,
                "did_ci_hi": r.did_ci_hi,
                "parallel_trends": r.parallel_trends,
                "parallel_trends_p": r.parallel_trends_p,
                "evidence_id": r.evidence_id,
                "clarity": r.clarity,
                "verdict": r.verdict,
                "note": "Layered DiD - see method docs; single-control tests cap clarity at 0.55.",
            }
            for r in facts.get("causal_results", [])
        ],
        "actions": [a.as_dict() for a in facts.get("actions", [])],
        "confidence": conf,
        "evidence": facts.get("evidence"),
        "freshness": facts.get("freshness"),
        "lineage": facts.get("lineage"),
        "abstain": conf["abstain"],
        "clarifying_question": conf.get("clarifying_question"),
        "delivery_channel": _delivery_channel("analyst", conf, "monitor"),
        "restricted_columns": facts.get("restricted_columns", []),
    }
