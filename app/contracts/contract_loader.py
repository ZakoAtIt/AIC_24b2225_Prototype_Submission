"""Loads kpi_contracts.json - the single source of truth for KPI semantics.

The pipeline MUST consult this file at runtime for thresholds, allowed
analytical methods, access policy and confidence policy. Nothing in the
analysis path may hardcode a threshold that exists in the contract.
"""

import json
from pathlib import Path

from app.config import DATA_DIR


class KPIContract:
    """Thin typed wrapper around one KPI's contract entry."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.kpi_id: str = raw["kpi_id"]
        self.name: str = raw["name"]
        self.formula: str = raw["formula"]
        self.unit: str = raw["unit"]
        self.owner: str = raw["owner"]
        self.dimensions: list = raw["dimensions"]
        self.time_grain: str = raw["time_grain"]
        self.sources: list = raw["sources"]
        self.freshness_sla_hours: int = raw["freshness_sla_hours"]
        self.materiality_threshold_pct: float = raw["materiality_threshold_pct"]
        self.statistical_threshold_zscore: float = raw["statistical_threshold_zscore"]
        self.known_drivers: list = raw["known_drivers"]
        self.controllable_drivers: list = raw["controllable_drivers"]
        self.access_policy: dict = raw["access_policy"]
        self.lineage: list = raw["lineage"]
        self.analytical_methods_allowed: list = raw["analytical_methods_allowed"]
        self.abstain_below: float = raw["confidence_policy"]["abstain_below"]

    def method_allowed(self, method: str) -> bool:
        return method in self.analytical_methods_allowed


def load_contracts() -> dict:
    """Returns {kpi_id: KPIContract}, read fresh from disk on every call."""
    path = Path(DATA_DIR) / "kpi_contracts.json"
    if not path.exists():
        raise FileNotFoundError("kpi_contracts.json missing - run generate_data.py")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k["kpi_id"]: KPIContract(k) for k in raw["kpis"]}


def get_contract(kpi_id: str) -> KPIContract:
    contracts = load_contracts()
    if kpi_id not in contracts:
        raise KeyError(f"Unknown KPI '{kpi_id}'. Valid: {sorted(contracts)}")
    return contracts[kpi_id]
