"""Evidence registry: every number the pipeline produces gets an ID.

This is the backbone of the "LLM cannot invent numbers" guarantee:
  1. Each pipeline stage registers its quantitative claims here.
  2. The narrative payload contains ONLY registry-backed values.
  3. llm_narrative's validator traces every number in the LLM output back to
     an evidence_id. Untraceable numbers -> validation failure -> safe fallback.
"""

import hashlib
import json
from datetime import datetime, timezone


class EvidenceRegistry:
    def __init__(self):
        self.items: list[dict] = []

    def add(
        self,
        claim: str,
        value=None,
        unit: str | None = None,
        source: str | None = None,
        method: str | None = None,
        method_type: str | None = None,
        freshness: dict | None = None,
        lineage: list | None = None,
        extra: dict | None = None,
    ) -> str:
        """Register one evidence item; returns its evidence_id (e.g. EV-0007)."""
        eid = f"EV-{len(self.items) + 1:04d}"
        item = {
            "evidence_id": eid,
            "claim": claim,
            "value": value,
            "unit": unit,
            "source": source,
            "method": method,
            "method_type": method_type,
            "freshness": freshness,
            "lineage": lineage or [],
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            item["details"] = extra
        self.items.append(item)
        return eid

    def find_evidence(self, number: float, shown_decimals: int) -> str | None:
        """Trace a number rendered by the LLM back to a registered value.

        Matching rule: the number must equal a registered value when rounded
        to the decimals actually displayed in the narrative. This makes the
        check strict about *displayed precision* but tolerant of formatting.
        """
        for item in self.items:
            v = item.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if round(float(v), shown_decimals) == round(float(number), shown_decimals):
                return item["evidence_id"]
        return None

    def as_payload(self) -> list[dict]:
        """Compact representation sent to the LLM and the /evidence endpoint."""
        return [
            {
                "evidence_id": it["evidence_id"],
                "claim": it["claim"],
                "value": it["value"],
                "unit": it["unit"],
                "source": it["source"],
                "method": it["method"],
                "method_type": it["method_type"],
            }
            for it in self.items
        ]


def cache_key(payload: dict) -> str:
    """Stable hash of a narrative payload - used by the response cache."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()
