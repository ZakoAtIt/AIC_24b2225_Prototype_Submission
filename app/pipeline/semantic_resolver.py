"""Pipeline stage 2 - semantic_resolver.

Reads kpi_contracts.json, validates that the requested KPI exists, enforces
the access policy (row-level region + column restrictions) for the simulated
user, and returns the scoped KPI frame. Method type: business_rules.

Simulated users (no real auth - role/region come from the request):
    role=cfo      -> entitled to ALL regions.
    role=analyst  -> bound to `user_region`; focusing any other region raises
                     AccessDenied. This is the demoable row-level-security beat.
Column-level example: analysts see gross_margin_pct but NOT raw unit_cost
(contract.access_policy.restricted_columns).
"""

from dataclasses import dataclass

from app.contracts.contract_loader import get_contract
from app.pipeline.load_and_validate import SourceCatalog, compute_kpi_frame

ALL_REGIONS = ["North", "South", "West"]


class AccessDenied(Exception):
    def __init__(self, message: str, user_region: str | None, attempted: str):
        super().__init__(message)
        self.user_region = user_region
        self.attempted = attempted


@dataclass
class ResolvedScope:
    contract: object
    kpi_frame: object            # tidy KPI frame already filtered to entitlement
    allowed_regions: list
    restricted_columns: list
    focus_label: str             # e.g. "South" or "ALL_REGIONS"
    pos_rows: object             # raw POS rows within scope (for decomposition/DiD)


def resolve_access(catalog: SourceCatalog, kpi_id: str, role: str,
                   user_region: str | None, focus_region: str | None = None) -> ResolvedScope:
    contract = get_contract(kpi_id)

    if role not in ("cfo", "analyst"):
        raise AccessDenied(f"Unknown role '{role}'", user_region, focus_region or "*")

    # --- row-level security ---------------------------------------------------
    if role == "cfo":
        visible_regions = ALL_REGIONS
        focus = focus_region or (user_region if user_region in ALL_REGIONS else None)
    else:  # analyst
        if not user_region or user_region not in ALL_REGIONS:
            raise AccessDenied("Analyst role requires a valid user_region", user_region,
                               focus_region or "*")
        visible_regions = [user_region]
        focus = focus_region or user_region
        if focus != user_region:
            raise AccessDenied(
                f"Analyst entitlement covers {user_region} only; cannot access '{focus}'",
                user_region, focus,
            )

    frame = compute_kpi_frame(catalog, kpi_id)

    scoped = frame[frame["region"].isin(visible_regions)] if "region" in frame.columns else frame
    if focus:
        focused = frame[frame["region"] == focus] if "region" in frame.columns else frame
        if focused.empty:
            raise AccessDenied(f"No accessible data for region '{focus}'", user_region, focus)
        label = focus
    else:
        focused = scoped
        label = "ALL_REGIONS"

    pos_source = catalog.get("pos_transactions") if contract.sources[0] == "pos_transactions" else None
    if pos_source is not None and focus:
        pos_rows = pos_source.df[pos_source.df["region"] == focus]
    elif pos_source is not None:
        pos_rows = pos_source.df[pos_source.df["region"].isin(visible_regions)]
    else:
        pos_rows = catalog.get("marketing_spend").df

    return ResolvedScope(
        contract=contract,
        kpi_frame=focused,
        allowed_regions=visible_regions,
        restricted_columns=list(contract.access_policy.get("restricted_columns", [])),
        focus_label=label,
        pos_rows=pos_rows,
    )
