"""Pipeline stage 8 - llm_narrative.

The ONLY step in the whole system where an LLM appears. Architecture contract:

  1. The LLM receives a structured JSON payload built exclusively from
     evidence-registry-backed facts. It never sees raw data and is forbidden
     from computing.
  2. System prompt hard-rules: no invented numbers; every quantitative claim
     cites an evidence_id; if abstain=true, ask the clarifying question -
     do NOT explain around insufficient evidence.
  3. Output is strict JSON. A deterministic validator traces EVERY number in
     the output back to an evidence_id. Any untraceable number -> validation
     failure -> safe templated fallback is served instead.
  4. Response caching is DISABLED by design: a fresh LLM call is made on every
     request so real-time data mutations are always reflected and nothing is
     ever replayed from the narrative_cache.

Providers (env LLM_PROVIDER): mock | github | openai | gemini.
The pipeline code is identical across providers - only client wiring differs.
'mock' is a first-class offline fallback that follows the same citation
discipline, so demos never depend on network or quota.

Method types logged to telemetry: retrieval (ticket snippet selection) + LLM.
"""

import json
import re

from app.config import (
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    GITHUB_MODELS_BASE_URL,
    GITHUB_MODELS_MODEL,
    GITHUB_TOKEN,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    MAX_TOKENS,
    OPENAI_API_KEY,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_PER_M,
    PROVIDER_PRICES,
)
from app.pipeline.evidence_registry import EvidenceRegistry

SYSTEM_PROMPT = """You are CauseTrace's insight narrator for a retail analytics engine.

STRICT RULES - violating any of these invalidates your output:
1. Do not invent ANY number not present in the input JSON. Not one.
2. Every quantitative claim you make MUST reference an evidence_id from the
   input's `evidence` array. Attach citations inline like [EV-0003].
3. If `abstain.abstain` is true, you must clearly communicate that the engine
   is NOT confident enough to explain, include the provided clarifying
   question verbatim, and NOT attempt to explain or speculate about causes.
4. Use only the drivers, effects, confidences, and cross_source_context given.
   Weave heterogeneous sources (POS, marketing, tickets) into the story when
   cross_source_context is present. Do not add causes, caveats about data
   quality, or business advice beyond `actions`.
5. Write for the requested persona: CFO = crisp headline + dollar impact +
   urgency; analyst = precise, method-aware detail.
6. Respect the sign language of the facts: a POSITIVE pct_deviation means the
   KPI moved UP vs its expected level; negative means DOWN. Never say "down"
   for an up-move.
7. Respond with JSON only, matching the provided schema exactly.
8. NO "metric soup": the headline and the summary MUST NOT mix relative
   percentages (%), percentage points (pp), and absolute dollars in the same
   sentence. Prioritize the ABSOLUTE dollar impact (e.g. USD/day) as the primary
   metric whenever it is available in the facts; if percentages are needed, put
   them in a separate sentence or in key_numbers only.

--- FORMATTING EXAMPLES (illustrative only - NEVER copy these numbers into
your answer; they exist purely to show tone, citation density and structure) ---

EXAMPLE A (persona=cfo, urgency=now):
{"headline":"South net revenue slipping ~$2,150/day against plan [EV-0001]",
 "summary":"A competitor promo is the dominant drag [EV-0004]; price held
  steady [EV-0002] and logistics is within band [EV-0003]. The loss is
  material at -1.8% vs expected [EV-0001] and urgent.
  Cross-source note: ticket sentiment in the region is down [EV-0005].",
 "key_numbers":[{"label":"Revenue impact","value":-2150,"evidence_id":"EV-0001"},
                {"label":"Price contribution","value":-40,"evidence_id":"EV-0002"},
                {"label":"Competitor contribution","value":-2100,"evidence_id":"EV-0004"}],
 "driver_story":"Units sold fell on the treated SKUs; the competitor activity
  test isolates the drag [EV-0004] rather than price [EV-0002].",
 "action_summary":"Validated counter-offer in South; sized A/B before scaling [EV-0006].",
 "urgency":"now"}

EXAMPLE B (persona=analyst, urgency=monitor):
{"headline":"Price effect within tolerance - monitor, do not act [EV-0010]",
 "summary":"DiD on the treated SKUs shows a small, non-significant impact
  (p=0.42) [EV-0011]; parallel-trends check passes [EV-0012], so the
  identification is clean but the effect is within noise.",
 "key_numbers":[{"label":"DiD effect","value":-34,"evidence_id":"EV-0010"},
                {"label":"p-value","value":0.42,"evidence_id":"EV-0011"},
                {"label":"Parallel-trends flag","value":1,"evidence_id":"EV-0012"}],
 "driver_story":"No single driver clears the significance bar; confidence is
  insufficient to rank causes.",
 "action_summary":"Hold price; reconfirm in the next window. No experiment
  proposed because the effect is not significant.",
 "urgency":"monitor"}"""

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "causetrace_narrative",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "summary": {"type": "string"},
                "key_numbers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "number"},
                            "evidence_id": {"type": "string"},
                        },
                        "required": ["label", "value", "evidence_id"],
                        "additionalProperties": False,
                    },
                },
                "driver_story": {"type": "string"},
                "action_summary": {"type": "string"},
                "urgency": {"type": "string", "enum": ["now", "this_week", "monitor"]},
            },
            "required": ["headline", "summary", "key_numbers", "driver_story",
                         "action_summary", "urgency"],
            "additionalProperties": False,
        },
    },
}

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Identifier patterns whose digits are NOT quantities. Applied IN ORDER:
# evidence citations (EV-0019), whole SKU clusters in any prose form
# ("SKU-101", "SKUs 101 and 102", "SKU 105/106", "SKU-101/102"), other coded
# ids with slash chains (ABC-12/13), legacy hyphenated forms (SKU-NEW-01),
# and ISO dates. Removed BEFORE number-scanning so citing evidence or naming
# a SKU isn't treated as hallucination.
_IDENT_RE = [
    re.compile(r"\bEV-\d{3,}\b"),
    re.compile(r"\bSKUs?\b(?:[\s:,#/-]*\d{1,6})*(?:\s*(?:and|&|or)\s*\d{1,6})*",
               re.IGNORECASE),
    re.compile(r"\b[A-Za-z]{2,}-\d+(?:/\d+)*\b"),
    re.compile(r"\b[A-Za-z]{2,}[A-Za-z]*-[A-Za-z0-9]*\d[\w-]*\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
]


def _strip_identifiers(s: str) -> str:
    for rx in _IDENT_RE:
        s = rx.sub(" ", s)
    return s

# KPIs where 'up' is the bad direction (drives adverse/favorable framing).
LOWER_IS_BETTER = {"return_rate", "customer_acquisition_cost"}


# ---------------------------------------------------------------------------
# Payload assembly (facts only, registry-backed)
# ---------------------------------------------------------------------------

def build_payload(kpi_meta: dict, movement: dict, drivers: list, did_results: list,
                  actions: list, confidence: dict, evidence: EvidenceRegistry,
                  persona: str) -> dict:
    return {
        "persona": persona,
        "kpi": kpi_meta,
        "movement": movement,
        "drivers": drivers,
        "causal_tests": [
            {
                "hypothesis": r.hypothesis,
                "effect_per_day_usd": r.did_effect,
                "effect_pct": r.did_effect_pct,
                "controls": r.controls,
                "clarity": r.clarity,
                "verdict": r.verdict,
            }
            for r in did_results
        ],
        "actions": [a.as_dict() for a in actions],
        "abstain": confidence,
        "evidence": evidence.as_payload(),
    }


# ---------------------------------------------------------------------------
# Validation - the anti-hallucination gate
# ---------------------------------------------------------------------------

def _trace(evidence: EvidenceRegistry, num: float, decimals: int) -> str | None:
    """Signed match first; then magnitude match (narratives render 'down 7.0%'
    while evidence stores -6.98); then a one-decimal relaxation for derived
    roundings. A hallucinated number will not coincide with ANY registered
    magnitude, so the guarantee holds."""
    for n in (num, -num):
        eid = evidence.find_evidence(n, decimals)
        if eid is not None:
            return eid
    for n in (num, -num):
        eid = evidence.find_evidence(n, max(decimals, 1))
        if eid is not None:
            return eid
    return None


def validate_narrative(narrative: dict, evidence: EvidenceRegistry) -> tuple[bool, list]:
    """Every numeric token anywhere in the narrative must trace to an
    evidence-registered value at the precision displayed."""
    violations = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for m in _NUM_RE.finditer(_strip_identifiers(node)):
                token = m.group(0)
                num = float(token.replace(",", ""))
                decimals = len(token.split(".")[1]) if "." in token else 0
                if _trace(evidence, num, decimals) is None:
                    violations.append({"where": path, "number": token})
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            # Numeric fields get the same tolerance ladder as strings: exact at
            # 2dp, then 1dp (renderers round freely), each with sign tolerance.
            if _trace(evidence, float(node), 2) is None and \
               _trace(evidence, float(node), 1) is None:
                violations.append({"where": path, "number": node})

    walk(narrative, "$")

    # key_numbers must cite valid evidence ids explicitly
    known_ids = {it["evidence_id"] for it in evidence.as_payload()}
    for kn in narrative.get("key_numbers", []):
        if kn.get("evidence_id") not in known_ids:
            violations.append({"where": "key_numbers", "number": kn.get("evidence_id")})

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Deterministic fact renderer - shared by mock provider AND safe fallback.
# Direction-aware: wording derives from the facts, never hardcoded.
# ---------------------------------------------------------------------------

def _framing(kpi_id: str, direction: str) -> str:
    """'adverse' / 'favorable' given the KPI's good direction."""
    if not direction:
        return "stable"
    adverse = (direction == "up") == (kpi_id in LOWER_IS_BETTER)
    return "adverse" if adverse else "favorable"


def _direction_word(pct: float) -> str:
    return "up" if pct >= 0 else "down"


def _level_word(pct: float) -> str:
    return "above" if pct >= 0 else "below"


def render_facts(payload: dict, cited: bool) -> dict:
    """Deterministic narrative built ONLY from payload numbers - therefore it
    always validates. `cited=True` attaches evidence ids (used by the mock
    provider); the safe fallback omits citations but keeps the same wording."""
    kpi, mov, conf = payload["kpi"], payload["movement"], payload["abstain"]
    pct = float(mov["pct_deviation"])
    dw, lw = _direction_word(pct), _level_word(pct)
    frame = _framing(mov.get("kpi_id", ""), mov.get("direction", ""))

    ev_ids = [e["evidence_id"] for e in payload["evidence"]]
    dev_ev = ev_ids[0] if ev_ids else "EV-0001"

    base = {
        "headline": (
            f"{kpi['name']} {dw} {abs(pct):.1f}% in {mov['scope']} - "
            f"{frame} material movement"
        ),
        "summary": (
            f"{kpi['name']} in {mov['scope']} is running {abs(pct):.1f}% {lw} its "
            f"expected level (z={mov['z_score']}, method {mov['method_used']}). "
            f"Composite confidence {conf['score']:.2f} ({conf['tier']})."
        ),
        "key_numbers": [],
        "driver_story": "",
        "action_summary": payload["actions"][0]["action"] if payload.get("actions") else "",
        "urgency": (
            "now" if abs(pct) >= 5 else
            "this_week" if abs(pct) >= 2 * kpi["materiality_threshold_pct"]
            else "monitor"
        ),
    }

    if conf.get("abstain"):
        base.update({
            "headline": f"{kpi['name']} ({mov['scope']}): analysis withheld pending clarification",
            "summary": (
                f"Composite confidence {conf['score']:.2f} ({conf['tier']}) is below the "
                f"abstention threshold {kpi['abstain_below']}. No explanation will be "
                f"offered until the clarifying question below is answered."
            ),
            "action_summary": "",
            "urgency": "monitor",
            "clarifying_question": conf.get("clarifying_question", ""),
        })
        return base

    top = payload["drivers"][0] if payload["drivers"] else None
    if top:
        base["driver_story"] = (
            f"Largest measured driver: {top['driver']} at {top['effect']:+,.2f} "
            f"{top['unit']} ({top['pct_of_movement']}% of the movement)."
        )

    if cited:
        base["key_numbers"] = [
            {"label": f"Deviation vs expected ({dw})", "value": round(pct, 1),
             "evidence_id": dev_ev},
            {"label": "Statistical score z", "value": float(mov["z_score"]),
             "evidence_id": dev_ev},
        ]
        parts = []
        for d in payload["drivers"][:3]:
            parts.append(
                f"{d['driver']} contributed {d['effect']:+,.2f} {d['unit']} "
                f"({d['pct_of_movement']}% of movement)"
            )
        if parts:
            base["driver_story"] = "; ".join(parts) + \
                ". Layered DiD controls separate concurrent shocks."
    else:
        base["_fallback_reason"] = base.get("_fallback_reason", "")

    return base


def safe_fallback(payload: dict, reason: str) -> dict:
    """Deterministic template used when the real LLM fails validation/parse or
    when policy abstains. Built ONLY from payload numbers, so it always
    validates."""
    out = render_facts(payload, cited=False)
    out["_fallback_reason"] = reason
    return out


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _client():
    if LLM_PROVIDER == "openai":
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SECONDS), LLM_MODEL
    if LLM_PROVIDER == "github":
        from openai import OpenAI
        return (OpenAI(api_key=GITHUB_TOKEN, base_url=GITHUB_MODELS_BASE_URL,
                       timeout=LLM_TIMEOUT_SECONDS),
                GITHUB_MODELS_MODEL)
    if LLM_PROVIDER == "gemini":
        from openai import OpenAI
        return (OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL,
                       timeout=LLM_TIMEOUT_SECONDS),
                GEMINI_MODEL)
    return None, LLM_MODEL


def _mock_narrative(payload: dict) -> dict:
    """Deterministic stand-in used for development/tests/offline demos
    (LLM_PROVIDER=mock). Same schema and citation discipline as the real
    model, so switching providers never changes downstream code."""
    p = render_facts(payload, cited=True)
    if payload["abstain"].get("abstain"):
        return p
    return p


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def _call_llm(payload: dict) -> tuple[dict | None, bool, list, dict, str]:
    """Call the configured provider with a model fallback chain. Returns
    (narrative|None, validated, violations, usage, model_used). Never raises:
    provider errors are converted to (None, ...) so the caller serves the
    deterministic fallback instead of crashing the analysis."""
    client, primary = _client()
    models = [primary] + (
        GEMINI_FALLBACK_MODELS if LLM_PROVIDER == "gemini" else []
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    last_err = None
    for model in models:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format=RESPONSE_SCHEMA,
                temperature=0.2,
                max_tokens=MAX_TOKENS,
            )
            usage = {"prompt_tokens": resp.usage.prompt_tokens,
                     "completion_tokens": resp.usage.completion_tokens}
            try:
                raw = resp.choices[0].message.content or ""
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
                narrative = json.loads(raw)
            except (json.JSONDecodeError, AttributeError, IndexError):
                narrative = None
            if narrative is None:
                return None, False, [{"where": "$", "number": "parse"}], usage, model
            return narrative, True, [], usage, model
        except Exception as e:                       # rate limit, network, auth
            last_err = e
            continue
    print(f"[causetrace] LLM unavailable ({last_err}); serving deterministic narration")
    return None, False, [], {"prompt_tokens": 0, "completion_tokens": 0}, primary


def generate_narrative(payload: dict, evidence: EvidenceRegistry) -> dict:
    """Returns {narrative, validated, violations, cache_hit, usage, cost_usd, model}.

    Caching is DISABLED by design: a fresh narrative is produced on every
    request so the LLM is always called instead of an identical payload being
    replayed from the narrative_cache. The returned schema and types are
    unchanged (cache_hit is always False).
    """
    if payload["abstain"].get("abstain"):
        # Abstention paths skip the LLM entirely: we serve the deterministic
        # abstention card so the clarifying question is guaranteed verbatim.
        # Deliberate cost+truth optimization.
        narrative = safe_fallback(payload, reason="abstain_policy")
        validated, violations = True, []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        used_model = "policy(deterministic)"
    elif LLM_PROVIDER == "mock":
        narrative = _mock_narrative(payload)
        validated, violations = validate_narrative(narrative, evidence)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        used_model = "mock"
    else:
        narrative, validated, violations, usage, used_model = _call_llm(payload)
        if narrative is None:
            # Provider failure (rate limit / network / parse): the analysis
            # MUST still serve - deterministic narration takes over and the
            # degradation is recorded instead of hidden.
            narrative = safe_fallback(payload, reason="llm_unavailable")
            validated, violations = False, [{"where": "$", "number": "provider_error"}]
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
            used_model = f"{LLM_PROVIDER}(unavailable)"
        else:
            validated, violations = validate_narrative(narrative, evidence)
            if not validated:
                narrative = safe_fallback(payload, reason="failed_number_validation")

    pin, pout = PROVIDER_PRICES.get(LLM_PROVIDER, (PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M))
    cost = (usage["prompt_tokens"] * pin
            + usage["completion_tokens"] * pout) / 1_000_000

    return {
        "narrative": narrative,
        "validated": validated,
        "violations": violations,
        "cache_hit": False,
        "usage": usage,
        "cost_usd": round(cost, 6),
        "model": used_model,
    }
