"""Pipeline stage 1 - load_and_validate.

Loads the three heterogeneous sources, resolves the frozen demo clock, checks
each source's freshness against its refresh cadence / SLA, and computes the
KPI series defined by the semantic contract.

Method type logged to telemetry: data_engineering.
"""

import os
import re
from dataclasses import dataclass, field

import pandas as pd

from app.config import DEMO_NOW_ENV
from app.services import store

# Refresh cadence -> freshness SLA (hours). The *source* has a cadence; each
# KPI inherits an SLA from its contract. We check both.
SOURCE_CADENCE_HOURS = {
    "pos_transactions": 24,
    "marketing_spend": 168,
    "support_tickets": 720,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class SourceInfo:
    name: str
    df: pd.DataFrame
    last_refresh: pd.Timestamp
    age_hours: float
    sla_hours: int
    stale: bool

    def freshness_factor(self) -> float:
        """1.0 when within SLA; decays linearly to a floor of 0.3 beyond it."""
        if not self.stale:
            return 1.0
        over = (self.age_hours - self.sla_hours) / max(self.sla_hours, 1)
        return max(0.3, 1.0 - 0.7 * min(over, 1.0))


@dataclass
class SourceCatalog:
    sources: dict[str, SourceInfo] = field(default_factory=dict)
    demo_now: pd.Timestamp | None = None

    def get(self, name: str) -> SourceInfo:
        return self.sources[name]

    def freshness_payload(self) -> list[dict]:
        return [
            {
                "source": s.name,
                "last_refresh": s.last_refresh.isoformat(),
                "age_hours": round(s.age_hours, 1),
                "sla_hours": s.sla_hours,
                "stale": s.stale,
                "grain": GRAIN_LABEL[s.name],
            }
            for s in self.sources.values()
        ]


GRAIN_LABEL = {
    "pos_transactions": "daily",
    "marketing_spend": "weekly",
    "support_tickets": "monthly",
}


def resolve_demo_now(catalog_hint: pd.Timestamp | None = None) -> pd.Timestamp:
    """Frozen clock: env DEMO_NOW if set, else newest POS date + 1 morning."""
    if DEMO_NOW_ENV:
        return pd.Timestamp(DEMO_NOW_ENV)
    if catalog_hint is not None:
        return catalog_hint + pd.Timedelta(days=1)
    pos = store.get_pos()
    return pos["date"].max() + pd.Timedelta(days=1)


def _last_pos_refresh(pos: pd.DataFrame) -> pd.Timestamp:
    return pos["date"].max() + pd.Timedelta(hours=6)  # nightly batch at 06:00


def _last_weekly_refresh(mkt: pd.DataFrame) -> pd.Timestamp:
    # week_start + 7 days - batch lands on the Monday after the week closes
    return mkt["week_start"].max() + pd.Timedelta(days=7)


def _last_monthly_refresh(tix: pd.DataFrame) -> pd.Timestamp:
    months = tix["month"].astype(str)
    latest = pd.Timestamp(months.max() + "-01") + pd.offsets.MonthBegin(1)
    return latest  # monthly batch lands on the 1st of the following month


def load_and_validate() -> SourceCatalog:
    pos = store.get_pos()
    mkt = store.get_marketing()
    tix = store.get_tickets()

    last_refreshes = {
        "pos_transactions": _last_pos_refresh(pos),
        "marketing_spend": _last_weekly_refresh(mkt),
        "support_tickets": _last_monthly_refresh(tix),
    }

    demo_now = resolve_demo_now(_last_pos_refresh(pos))

    catalog = SourceCatalog(demo_now=demo_now)
    dfs = {"pos_transactions": pos, "marketing_spend": mkt, "support_tickets": tix}
    for name, df in dfs.items():
        sla = SOURCE_CADENCE_HOURS[name]
        lr = last_refreshes[name]
        age = max(0.0, (demo_now - lr).total_seconds() / 3600.0)
        catalog.sources[name] = SourceInfo(
            name=name,
            df=df,
            last_refresh=lr,
            age_hours=age,
            sla_hours=sla,
            stale=age > sla,
        )
    return catalog


# ---------------------------------------------------------------------------
# KPI computation from raw sources. Formulas mirror kpi_contracts.json exactly;
# tests/test_waterfall_math.py cross-checks them against the contract formula.
# ---------------------------------------------------------------------------

# KPI frames are intentionally NOT cached: the source CSVs are re-read from
# disk on every request, so cached frames would serve stale snapshots during
# chaos testing. Every call recomputes from the freshest data.
def compute_kpi_frame(catalog: SourceCatalog, kpi_id: str) -> pd.DataFrame:
    """Return a tidy frame: period column + dimension columns + `value`.

    daily KPIs  -> period col `date`,   grain from POS
    weekly KPIs -> period col `week_start`, grain from marketing source

    Always recomputes from the current catalog data - never cached.
    """
    return _compute_kpi_frame_uncached(catalog, kpi_id)


def _compute_kpi_frame_uncached(catalog: SourceCatalog, kpi_id: str) -> pd.DataFrame:
    if kpi_id == "customer_acquisition_cost":
        mkt = catalog.get("marketing_spend").df
        g = mkt.groupby(["week_start", "region"], as_index=False).agg(
            spend=("spend_usd", "sum"), new_custs=("new_customers_attributed", "sum")
        )
        denom = g["new_custs"].where(g["new_custs"] > 0)
        g["value"] = g["spend"].div(denom).fillna(0.0)
        g = g.rename(columns={"week_start": "period"})
        g["period"] = pd.to_datetime(g["period"])
        return g[["period", "region", "value"]]

    pos = catalog.get("pos_transactions").df
    pos = pos.copy()
    pos["gross"] = pos["units_sold"] * pos["unit_price"]
    pos["margin_usd"] = pos["units_sold"] * (pos["unit_price"] - pos["unit_cost"])

    dims_by_kpi = {
        "net_revenue": ["region"],
        "units_sold": ["region"],
        "return_rate": ["region"],
        "gross_margin_pct": ["region"],
    }
    dims = dims_by_kpi[kpi_id]
    g = pos.groupby(["date"] + dims, as_index=False).agg(
        gross=("gross", "sum"),
        margin_usd=("margin_usd", "sum"),
        returns_value=("returns_value", "sum"),
        units=("units_sold", "sum"),
        returns_units=("returns_units", "sum"),
    )

    if kpi_id == "net_revenue":
        g["value"] = g["gross"] - g["returns_value"]
    elif kpi_id == "units_sold":
        g["value"] = g["units"].astype(float)
    elif kpi_id == "return_rate":
        g["value"] = g["returns_units"] / g["units"] * 100.0
    elif kpi_id == "gross_margin_pct":
        # ratio KPI: keep the additive parts so aggregation stays correct
        net = (g["gross"] - g["returns_value"]).replace(0, pd.NA)
        g["net"] = (g["gross"] - g["returns_value"]).astype(float)
        g["value"] = ((g["margin_usd"] - g["returns_value"]) / net * 100.0).astype(float)

    out = g.rename(columns={"date": "period"})
    out["period"] = pd.to_datetime(out["period"])
    keep = ["period"] + dims + ["value"]
    if kpi_id == "gross_margin_pct":
        keep += ["net", "margin_usd", "returns_value"]
    return out[keep].dropna(subset=["value"])
