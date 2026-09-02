"""Human-correction intent mapper.

Turns a free-text correction ("the competitor discount in South took our
volume") into a NORMALIZED driver from the contract taxonomy, so the learning
loop integrates human language into structured machinery:

    plain text -> matched driver (+confidence) -> feedback row -> live weight
    nudge -> damped action-ranking boost -> correction-memory chips -> cited
    as 'analyst-flagged' context in subsequent analyses

Two implementations behind one interface (explicitly labeled everywhere):
    * LLM   - gemini/gpt classifies into the vocabulary (used when a real
              provider is configured). Output is validated against the
              vocabulary; anything unexpected falls back to rules.
    * rules - deterministic keyword scorer over the same vocabulary (offline
              demo path).

The vocabulary is DERIVED from kpi_contracts.json (known_drivers across all
KPIs) - the semantic contract stays the single source of truth.

Method type logged to telemetry: LLM | business_rules.
"""

import json

from app.config import GEMINI_API_KEY, GEMINI_BASE_URL, GEMINI_MODEL, LLM_PROVIDER
from app.contracts.contract_loader import load_contracts

# Keyword scorer over the canonical driver vocabulary (deterministic path).
KEYWORDS = {
    "competitor_activity": [
        "competitor", "rival", "their promo", "their discount", "market share",
        "undercut", "other brand",
    ],
    "price": ["price", "pricing", "list price", "elasticity", "rollback",
              "our increase", "price up"],
    "marketing_spend": ["marketing", "ads", "ad spend", "campaign", "media",
                        "cac", "acquisition", "creative"],
    "logistics_performance": ["logistics", "delivery", "carrier", "shipping",
                              "freight", "fulfillment", "warehouse", "transit",
                              "delay"],
    "returns_rate": ["returns", "return rate", "defect", "quality issue",
                     "damaged", "sizing", "broken"],
    "seasonality": ["season", "seasonal", "holiday", "weather", "summer", "winter"],
    "stockouts": ["stockout", "stock out", "inventory", "out of stock", "supply"],
    "mix": ["mix", "channel shift", "product blend"],
    "cogs_mix": ["cogs", "cost of goods", "input cost", "sourcing cost"],
}

SYSTEM_PROMPT = """You map a business user's free-text correction about a KPI
movement onto ONE driver label from the provided vocabulary. Respond with JSON
only: {"driver": "<label-or-null>", "confidence": <0..1>}. Choose null if no
vocabulary item fits. Never invent new labels."""


def driver_vocabulary(contracts=None) -> list[str]:
    """Union of known_drivers across all KPI contracts (order-stable)."""
    contracts = contracts or load_contracts()
    vocab = []
    for kpi_id, contract in contracts.items():
        for d in contract.known_drivers:
            if d not in vocab:
                vocab.append(d)
    return vocab


def _rule_score(text: str, vocab: list[str]) -> tuple[str | None, float, list]:
    lowered = f" {text.lower()} "
    scores = {}
    for driver in vocab:
        s = sum(1 for kw in KEYWORDS.get(driver, []) if kw in lowered)
        if s:
            scores[driver] = s
    if not scores:
        return None, 0.0, []
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top_driver, top_score = ranked[0]
    total = sum(scores.values())
    confidence = round(top_score / max(total, 1) * min(1.0, top_score / 2.0), 2)
    return top_driver, max(confidence, 0.4), [d for d, _ in ranked[1:3]]


def _llm_classify(text: str, vocab: list[str]) -> tuple[str | None, float]:
    from openai import OpenAI
    client = OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL,
                    timeout=15)
    prompt = (f"{SYSTEM_PROMPT}\n\nVocabulary: {json.dumps(vocab)}\n\n"
              f"Correction: \"{text}\"")
    try:
        resp = client.chat.completions.create(
            model=GEMINI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=100,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        driver, conf = data.get("driver"), float(data.get("confidence", 0))
        if driver in vocab and 0 <= conf <= 1:
            return driver, conf
    except Exception:
        pass
    return None, 0.0


def map_correction(text: str, contracts) -> dict:
    """Returns an integration-ready mapping result with explicit provenance."""
    vocab = driver_vocabulary(contracts)

    driver, confidence, alternatives = None, 0.0, []
    method_type = "business_rules"
    if LLM_PROVIDER != "mock" and GEMINI_API_KEY:
        driver, confidence = _llm_classify(text, vocab)
        if driver is not None:
            method_type = "LLM"
        else:
            method_type = "business_rules(fallback)"
    if driver is None:
        driver, confidence, alternatives = _rule_score(text, vocab)

    return {
        "matched_driver": driver,
        "match_confidence": confidence,
        "method_type": method_type,
        "alternatives": alternatives,
        "vocabulary": vocab,
        "raw_text": text.strip(),
    }
