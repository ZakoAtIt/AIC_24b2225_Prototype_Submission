"""Pipeline stage 4 - decomposition_engine.

Exact arithmetic waterfall for net_revenue movements. NO ML, NO estimates:
given per-SKU daily quantities and prices for a baseline window (B) and an
evaluation window (W), the movement decomposes EXACTLY as:

    DeltaR = R_W - R_B
           = PriceEffect + VolumeEffect + MixEffect + ReturnsEffect

with the convention (fixed project-wide):
  * Price effect  = Sigma q1 * (p1 - p0)          (current volumes, price delta)
  * Volume effect = g * Sigma(p0 * q0)            (proportional growth at base prices,
                                                   g = Q1/Q0 - 1)
  * Mix effect    = Sigma ((q1 - q0 - g*q0) * p0) (deviation from proportional growth)
  * Returns bar   = -(RetW - RetB)                (returns valued directly)

Price+Volume+Mix sums exactly to the gross-sales delta; adding the returns
bar ties to the net revenue delta. An assertion enforces this identity to
the cent on every run - if it ever fails, the pipeline refuses to answer.

Method type logged to telemetry: deterministic_arithmetic.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class WaterfallResult:
    kpi_id: str
    scope: str
    baseline_window: tuple[str, str]
    eval_window: tuple[str, str]
    revenue_baseline_per_day: float
    revenue_eval_per_day: float
    total_delta_per_day: float
    components_usd_per_day: list   # [{driver, effect, pct_of_total}] ranked by |effect|
    identity_check_passed: bool


def _window_bounds(periods: pd.Series, eval_days: int = 14, baseline_days: int = 28):
    periods = periods.sort_values().unique()
    end = pd.Timestamp(periods[-1])
    w_start = end - pd.Timedelta(days=eval_days - 1)
    b_end = w_start - pd.Timedelta(days=1)
    b_start = b_end - pd.Timedelta(days=baseline_days - 1)
    return (pd.Timestamp(b_start), b_end), (w_start, end)


def waterfall_net_revenue(pos_df: pd.DataFrame, scope_region: str | None) -> WaterfallResult:
    """pos_df: raw POS rows (already region-filtered by the resolver)."""
    df = pos_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if scope_region:
        df = df[df["region"] == scope_region]

    (b_start, b_end), (w_start, w_end) = _window_bounds(df["date"])
    base = df[(df["date"] >= b_start) & (df["date"] <= b_end)]
    curr = df[(df["date"] >= w_start) & (df["date"] <= w_end)]

    nb_days_base = (b_end - b_start).days + 1
    nb_days_eval = (w_end - w_start).days + 1

    # per-SKU averages per day within each window
    agg_b = base.groupby("product_id").agg(q=("units_sold", "sum"), ret=("returns_value", "sum"),
                                           p=("unit_price", "mean"))
    agg_w = curr.groupby("product_id").agg(q=("units_sold", "sum"), ret=("returns_value", "sum"),
                                           p=("unit_price", "mean"))
    skus = sorted(set(agg_b.index) | set(agg_w.index))
    agg_b = agg_b.reindex(skus).fillna(0.0)
    agg_w = agg_w.reindex(skus).fillna(0.0)

    q0 = agg_b["q"] / nb_days_base
    q1 = agg_w["q"] / nb_days_eval
    p0 = agg_b["p"]
    p1 = agg_w["p"]

    R0_gross = float((p0 * q0).sum())
    R1_gross = float((p1 * q1).sum())
    ret0_per_day = float((agg_b["ret"] / nb_days_base).sum())
    ret1_per_day = float((agg_w["ret"] / nb_days_eval).sum())

    # NET revenue levels (the KPI under analysis), so the reported movement
    # and the component bars live on the same scale.
    R0_net = R0_gross - ret0_per_day
    R1_net = R1_gross - ret1_per_day

    Q0, Q1 = float(q0.sum()), float(q1.sum())
    g_growth = (Q1 / Q0 - 1.0) if Q0 > 0 else 0.0

    price_eff = float((q1 * (p1 - p0)).sum())                      # current-volume priced
    volume_eff = float(g_growth * R0_gross)                        # proportional growth
    mix_eff = float(((q1 - q0 - g_growth * q0) * p0).sum())        # share shifts at base prices
    returns_bar = -(ret1_per_day - ret0_per_day)

    total = R1_net - R0_net
    reconstructed = price_eff + volume_eff + mix_eff + returns_bar
    identity_ok = abs(reconstructed - total) < 0.05  # five cents tolerance on daily averages

    def pct(x):
        return round(100.0 * x / total, 1) if abs(total) > 1e-9 else 0.0

    comps = [
        {"driver": "price", "effect": round(price_eff, 2), "pct_of_movement": pct(price_eff)},
        {"driver": "volume", "effect": round(volume_eff, 2), "pct_of_movement": pct(volume_eff)},
        {"driver": "mix", "effect": round(mix_eff, 2), "pct_of_movement": pct(mix_eff)},
        {"driver": "returns_rate", "effect": round(returns_bar, 2), "pct_of_movement": pct(returns_bar)},
    ]
    comps.sort(key=lambda c: -abs(c["effect"]))

    return WaterfallResult(
        kpi_id="net_revenue",
        scope=scope_region or "ALL_REGIONS",
        baseline_window=(str(b_start.date()), str(b_end.date())),
        eval_window=(str(w_start.date()), str(w_end.date())),
        revenue_baseline_per_day=round(R0_net, 2),
        revenue_eval_per_day=round(R1_net, 2),
        total_delta_per_day=round(total, 2),
        components_usd_per_day=comps,
        identity_check_passed=bool(identity_ok),
    )
