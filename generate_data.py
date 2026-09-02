"""CauseTrace synthetic world generator.

Creates, on first run, inside ./data/:
  - pos_transactions.csv   (daily grain, refreshed daily)
  - marketing_spend.csv    (weekly grain, refreshed weekly)
  - support_tickets.csv    (monthly grain, refreshed monthly)
  - events.json            (ground truth - DEBUG/TEST ONLY, never read by the pipeline)
  - kpi_contracts.json     (the semantic contract - the pipeline DOES read this)

GROUND TRUTH FIREWALL
---------------------
events.json is written ONLY for (a) the /events transparency endpoint and
(b) automated recovery tests. No module under app/pipeline may import or read
it; tests/test_architecture_firewall.py fails the suite if that ever changes.

DESIGN NOTES (why two columns exist beyond the original brief)
--------------------------------------------------------------
* pos_transactions gains `unit_cost`      -> required to compute gross_margin_pct,
                                             and enables the column-level security demo.
* marketing_spend gains `new_customers_attributed` -> required to compute
                                             customer_acquisition_cost.
Both extensions are documented in README.md.

Everything is seeded (SEED=42): every regeneration produces the identical world.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
DATA_DIR = Path(__file__).resolve().parent / "data"

START_DATE = pd.Timestamp("2026-04-01")          # day 1
N_DAYS = 120                                      # days 1..120
END_DATE = START_DATE + pd.Timedelta(days=N_DAYS - 1)  # 2026-07-29
DEMO_NOW_SUGGESTED = "2026-07-30"                 # max data date + 1 morning

# --- Event windows (inclusive), expressed as dates ---------------------------
# All three shocks are ACTIVE at DEMO_NOW (2026-07-30) so the detector's
# trailing evaluation window sees them. This is deliberate demo design.
PROMO_START, PROMO_END = "2026-07-17", "2026-07-29"       # E1 competitor promo, South (ongoing)
PRICE_CHANGE_START = "2026-07-20"                          # E2 price increase, South, 2 SKUs
LOGI_START, LOGI_END = "2026-07-19", "2026-07-29"          # E3 logistics delay, West (ongoing)
LAUNCH_DATE = "2026-07-09"                                 # E5 SKU-NEW-01 launch, North
SURGE_WEEKS = ["2026-07-13", "2026-07-20"]                 # E1b defensive media surge, South

REGIONS = ["North", "South", "West"]
REGION_VOLUME_SHARE = {"North": 0.35, "South": 0.40, "West": 0.25}

# product_id, name, list_price_usd, unit_cost_usd, base_daily_units, return_rate
PRODUCTS = [
    ("SKU-101", "Aurora Desk Lamp",   42.0, 24.0, 320, 0.03),
    ("SKU-102", "Nimbus Bottle",      28.0, 11.0, 550, 0.04),
    ("SKU-103", "Halo Phone Stand",   19.0,  7.5, 700, 0.05),
    ("SKU-104", "Drift Yoga Mat",     55.0, 26.0, 150, 0.09),
]
NEW_SKU = ("SKU-NEW-01", "Lumen Smart Mug", 65.0, 34.0, 45, 0.02)
PRICE_TREATED_SKUS = ["SKU-101", "SKU-102"]        # E2 applies only to these, South only

CHANNEL_SHARES = {"web": 0.55, "marketplace": 0.30, "retail": 0.15}
WEEKDAY_MULT = [0.95, 0.92, 0.90, 1.00, 1.15, 1.35, 1.28]  # Mon..Sun
TREND_PER_DAY = 0.0008                             # gentle organic growth (~+10% over horizon)
NOISE_SIGMA = 0.02                                 # daily multiplicative noise (halved for DiD power)


def _apply_events(df: pd.DataFrame) -> pd.DataFrame:
    """Embed the planted ground-truth effects into the POS rows."""
    d = pd.to_datetime(df["date"])

    # E2: price increase in South on treated SKUs from PRICE_CHANGE_START.
    # Modest short-run elasticity (~0.25) -> units divide by ~1.02 for a +8% move,
    # so the price is locally revenue-POSITIVE and DiD can see that cleanly.
    treat_mask = (
        (df["region"] == "South")
        & (df["product_id"].isin(PRICE_TREATED_SKUS))
        & (d >= pd.Timestamp(PRICE_CHANGE_START))
    )
    df.loc[treat_mask, "unit_price"] = (df.loc[treat_mask, "unit_price"] * 1.08).round(2)
    df.loc[treat_mask, "units_sold"] = np.ceil(
        df.loc[treat_mask, "units_sold"] / 1.02
    ).astype(int)

    # E1: competitor promo suppresses ALL South units during the window.
    promo_mask = (
        (df["region"] == "South")
        & (d >= pd.Timestamp(PROMO_START))
        & (d <= pd.Timestamp(PROMO_END))
    )
    df.loc[promo_mask, "units_sold"] = np.floor(df.loc[promo_mask, "units_sold"] * 0.75).astype(int)

    # E3: logistics delay suppresses West units and damages some deliveries.
    logi_mask = (
        (df["region"] == "West")
        & (d >= pd.Timestamp(LOGI_START))
        & (d <= pd.Timestamp(LOGI_END))
    )
    df.loc[logi_mask, "units_sold"] = np.floor(df.loc[logi_mask, "units_sold"] * 0.90).astype(int)
    # Massive return-rate spike: +4.0 percentage points (a flat add, not a weak
    # multiplicative nudge) so the West DiD is unambiguous in both directions.
    df.loc[logi_mask, "_return_rate"] = df.loc[logi_mask, "_return_rate"] + 0.04

    return df


def generate_pos(seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 1)
    dates = pd.date_range(START_DATE, periods=N_DAYS, freq="D")

    rows = []
    for date in dates:
        trend = 1.0 + TREND_PER_DAY * (date - START_DATE).days
        seasonal = WEEKDAY_MULT[date.dayofweek]

        active_products = list(PRODUCTS)
        if date >= pd.Timestamp(LAUNCH_DATE):
            # E5: new SKU ramps 10 -> 45 units/day over its first 3 weeks.
            age = (date - pd.Timestamp(LAUNCH_DATE)).days
            ramped = (NEW_SKU[0], NEW_SKU[1], NEW_SKU[2], NEW_SKU[3], min(45, 10 + age * 2), NEW_SKU[5])
            active_products.append(ramped)

        for pid, pname, price, cost, base_units, ret_rate in active_products:
            region_units_base = base_units * REGION_VOLUME_SHARE["North"]
            for region in REGIONS:
                expected = base_units * REGION_VOLUME_SHARE[region] * trend * seasonal
                units_day_region = int(max(0, round(expected * rng.normal(1.0, NOISE_SIGMA))))
                if units_day_region == 0:
                    continue
                # split across channels with small noise, then emit one row each
                raw = np.array([CHANNEL_SHARES[c] for c in CHANNEL_SHARES])
                raw = raw * rng.normal(1.0, 0.04, size=raw.size)
                raw = np.clip(raw, 0.01, None)
                shares = raw / raw.sum()
                chan_units = np.floor(units_day_region * shares).astype(int)
                chan_units[0] += units_day_region - chan_units.sum()  # absorb rounding

                today_price = price  # flat list price unless an event moves it
                rr_today = ret_rate
                for (chan, u) in zip(CHANNEL_SHARES.keys(), chan_units):
                    if u <= 0:
                        continue
                    rows.append({
                        "date": date.strftime("%Y-%m-%d"),
                        "region": region,
                        "product_id": pid,
                        "product_name": pname,
                        "channel": chan,
                        "units_sold": int(u),
                        "unit_price": round(float(today_price), 2),
                        "returns_units": 0,           # filled after event pass
                        "returns_value": 0.0,         # filled after event pass
                        "unit_cost": round(float(cost), 2),
                        "_return_rate": rr_today,     # temp col, dropped before save
                    })

    df = pd.DataFrame(rows)
    df = _apply_events(df)

    # Returns are drawn AFTER event effects so they track realized units.
    rng2 = np.random.default_rng(seed + 2)
    ret_units = np.floor(df["units_sold"] * df["_return_rate"] * rng2.normal(1.0, 0.25, len(df)))
    df["returns_units"] = np.clip(ret_units, 0, df["units_sold"]).astype(int)
    df["returns_value"] = (df["returns_units"] * df["unit_price"]).round(2)

    out = df.drop(columns=["_return_rate"])
    out = out[[  # exact column order
        "date", "region", "product_id", "product_name", "channel",
        "units_sold", "unit_price", "returns_units", "returns_value", "unit_cost",
    ]]
    return out.sort_values(["date", "region", "product_id"]).reset_index(drop=True)


def generate_marketing(seed: int = SEED) -> pd.DataFrame:
    """Weekly marketing spend with a documented defensive surge (E1b).

    `new_customers_attributed` is emitted by the marketing platform's own
    attribution; efficiency drops during the surge weeks so CAC rises in South.
    """
    rng = np.random.default_rng(seed + 3)
    week_starts = pd.date_range("2026-04-06", "2026-07-27", freq="W-MON")

    base = {  # (region, channel): weekly spend baseline
        ("North", "paid_social"): 5200, ("North", "search"): 4600, ("North", "influencer"): 2100,
        ("South", "paid_social"): 6400, ("South", "search"): 5100, ("South", "influencer"): 2600,
        ("West", "paid_social"): 3800, ("West", "search"): 3300, ("West", "influencer"): 1500,
    }
    campaign_names = {
        "paid_social": "Always-On Social Prospecting",
        "search": "Brand + Generic Search",
        "influencer": "Creator Seeding Program",
    }

    rows = []
    for ws in week_starts:
        for (region, channel), spend0 in base.items():
            spend = spend0 * rng.normal(1.0, 0.08)
            eff = 1.0  # customers per dollar factor
            if region == "South" and channel == "paid_social" and ws.strftime("%Y-%m-%d") in SURGE_WEEKS:
                spend *= 2.2   # E1b defensive surge
                eff /= 1.35    # rushed creative -> worse efficiency
            spend = round(float(spend), 2)
            new_custs = max(1, int(round(spend / 45.0 * eff * rng.normal(1.0, 0.12))))
            rows.append({
                "week_start": ws.strftime("%Y-%m-%d"),
                "region": region,
                "channel": channel,
                "campaign_name": campaign_names[channel],
                "spend_usd": spend,
                "new_customers_attributed": new_custs,
            })
    return pd.DataFrame(rows)


PRAISE = [
    "Exactly as described, arrived fast. Would buy again.",
    "Great quality for the price - my third order this year.",
    "Packaging was tidy and delivery tracking was accurate.",
    "Customer support swapped my size in two days. Impressed.",
]
MIXED = [
    "Product is fine, delivery estimate slipped a few days.",
    "Decent value but the color looks different in person.",
    "Works as expected, nothing special either way.",
]
COMPLAIN_DELAY = [
    "Package stuck 'in transit' for 12 days now. Support unreachable.",
    "Ordered two weeks ago, still no delivery and no update.",
    "Courier says delayed, no new date given. Frustrating.",
]


def generate_tickets(pos: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 4)
    months = ["2026-04", "2026-05", "2026-06", "2026-07"]
    monthly_units = pos.assign(month=pos["date"].str[:7]).groupby(["month", "region"])["units_sold"].sum()

    rows = []
    for month in months:
        for region in REGIONS:
            scale = float(monthly_units.get((month, region), 50_000)) / 50_000.0
            pids = [p[0] for p in PRODUCTS] + (["SKU-NEW-01"] if month == "2026-07" else [])
            for pid in pids:
                tickets = int(max(3, rng.normal(28 * scale, 4)))
                sentiment = float(np.clip(rng.normal(0.25, 0.12), -1, 1))

                # E4: July South complaint wave about fulfillment delays.
                # Deliberately NO matching move in structured sales KPIs yet.
                if month == "2026-07" and region == "South":
                    tickets = int(tickets * 2.5)
                    sentiment = -0.55 + rng.normal(0, 0.04)
                    pool = COMPLAIN_DELAY
                elif sentiment > 0.15:
                    pool = PRAISE
                else:
                    pool = MIXED

                rows.append({
                    "month": month,
                    "region": region,
                    "product_id": pid,
                    "ticket_count": tickets,
                    "avg_sentiment_score": round(sentiment, 3),
                    "sample_ticket_text": pool[int(rng.integers(0, len(pool)))],
                })
    return pd.DataFrame(rows)


def build_events_json(scenario: str = "default") -> dict:
    events = [
            {
                "event_id": "E1",
                "name": "Competitor promotion",
                "type": "competitor_activity",
                "region": "South",
                "start": PROMO_START, "end": PROMO_END,
                "effect": {"units_multiplier": 0.75, "scope": "all SKUs"},
                "expected_signature": "South units/revenue dip of roughly -25%, ongoing through DEMO_NOW",
            },
            {
                "event_id": "E2",
                "name": "List price increase (+8%)",
                "type": "price_change",
                "region": "South",
                "start": PRICE_CHANGE_START, "end": None,
                "effect": {
                    "price_multiplier": 1.08,
                    "skus": PRICE_TREATED_SKUS,
                    "elasticity_units_divisor": 1.02,
                },
                "expected_signature": (
                    "Higher unit price on SKU-101/SKU-102 in South; mild unit softness on "
                    "treated SKUs only - identifiable vs the promo via within-region comparison"
                ),
            },
            {
                "event_id": "E3",
                "name": "Logistics delay",
                "type": "supply_disruption",
                "region": "West",
                "start": LOGI_START, "end": LOGI_END,
                "effect": {"units_multiplier": 0.90, "return_rate_add_pp": 4.0},
                "expected_signature": "West units dip ~-10%; return rate spiked by ~+4.0pp relative",
            },
            {
                "event_id": "E1b",
                "name": "Defensive media surge",
                "type": "marketing_spend_change",
                "region": "South",
                "start": SURGE_WEEKS[0], "end": "2026-07-12",
                "effect": {"spend_multiplier": 2.2, "channel": "paid_social", "cac_penalty": 1.35},
                "expected_signature": "South paid_social spend spike; CAC inflation same weeks",
            },
            {
                "event_id": "E4",
                "name": "Fulfillment complaint wave",
                "type": "sentiment_shift",
                "region": "South",
                "start": "2026-07-01", "end": "2026-07-31",
                "effect": {"avg_sentiment": -0.55, "ticket_multiplier": 2.5},
                "note": "CONTRADICTION CASE: unstructured signal negative while structured sales show no material movement yet.",
            },
            {
                "event_id": "E5",
                "name": "New product launch",
                "type": "launch",
                "region": "North",
                "start": LAUNCH_DATE, "end": None,
                "skus": ["SKU-NEW-01"],
                "note": "Sparse-history scenario: 21 days of history at DEMO_NOW.",
            },
        ]

    if scenario == "unknown_factor":
        win_end = pd.to_datetime(END_DATE).strftime("%Y-%m-%d")
        win_start = (pd.to_datetime(END_DATE) - pd.Timedelta(days=13)).strftime("%Y-%m-%d")
        events.append({
            "event_id": "U1",
            "name": "Unmodelled North demand shock",
            "type": "unknown_factor",
            "region": "North",
            "start": win_start, "end": win_end,
            "effect": {"units_multiplier": 0.85, "scope": "all North SKUs"},
            "expected_signature": "North units dip ~-15% over the final 14 days with NO known driver active; pipeline should ABSTAIN.",
            "true_driver": "unmodeled_shock",
        })
    elif scenario == "adversarial_trap":
        win_end = pd.to_datetime(END_DATE).strftime("%Y-%m-%d")
        win_start = (pd.to_datetime(END_DATE) - pd.Timedelta(days=13)).strftime("%Y-%m-%d")
        events.append({
            "event_id": "T1",
            "name": "Competitor activity drives South revenue drop",
            "type": "competitor_activity",
            "region": "South",
            "start": win_start, "end": win_end,
            "effect": {"net_revenue_multiplier": 0.90, "scope": "all South SKUs"},
            "expected_signature": "South net-revenue dip ~-10% over the final 14 days.",
            "true_driver": "competitor_activity",
        })
        events.append({
            "event_id": "T2",
            "name": "Global marketing spend spike (FALSE POSITIVE LURE)",
            "type": "marketing_spend_change",
            "region": "ALL",
            "start": win_start, "end": win_end,
            "effect": {"spend_multiplier": 1.40, "scope": "all regions, all channels"},
            "expected_signature": "Worldwide paid spend +40%; does NOT cause the South revenue drop. NOT a true driver of any revenue change.",
            "true_driver": None,
            "is_lure": True,
        })

    return {
        "_meta": {
            "warning": (
                "GROUND TRUTH - debug/test artifact only. The analysis pipeline "
                "(app/pipeline/*) must NEVER read this file. It powers the /events "
                "debug endpoint and automated recovery tests."
            ),
            "seed": SEED,
            "world": f"{START_DATE.date()} .. {END_DATE.date()}",
            "scenario": scenario,
        },
        "events": events,
    }


KPI_CONTRACTS = {
    "_meta": {
        "description": (
            "Single source of truth for KPI semantics. The backend loads this file "
            "and enforces thresholds, allowed methods and access policy AT RUNTIME."
        ),
        "version": "1.0.0",
    },
    "kpis": [
        {
            "kpi_id": "net_revenue",
            "name": "Net Revenue",
            "formula": "sum(units_sold * unit_price) - sum(returns_value)",
            "unit": "USD",
            "owner": "VP Sales",
            "dimensions": ["region", "product", "channel"],
            "time_grain": "daily",
            "sources": ["pos_transactions"],
            "freshness_sla_hours": 24,
            "materiality_threshold_pct": 3.0,
            "statistical_threshold_zscore": 2.0,
            "known_drivers": ["price", "volume", "mix", "returns_rate", "marketing_spend", "competitor_activity"],
            "controllable_drivers": ["price", "marketing_spend"],
            "access_policy": {"row_level": "region", "restricted_columns": []},
            "lineage": ["pos_transactions.raw -> daily_aggregation -> net_revenue"],
            "analytical_methods_allowed": ["waterfall_decomposition", "STL_anomaly", "DiD_causal_test"],
            "confidence_policy": {"abstain_below": 0.6},
        },
        {
            "kpi_id": "gross_margin_pct",
            "name": "Gross Margin %",
            "formula": "(sum(units_sold * (unit_price - unit_cost)) - sum(returns_value)) / sum(units_sold * unit_price) * 100",
            "unit": "percent",
            "owner": "CFO Office",
            "dimensions": ["region", "product"],
            "time_grain": "daily",
            "sources": ["pos_transactions"],
            "freshness_sla_hours": 24,
            "materiality_threshold_pct": 2.0,
            "statistical_threshold_zscore": 2.0,
            "known_drivers": ["price", "cogs_mix", "returns_rate", "discounting"],
            "controllable_drivers": ["price"],
            "access_policy": {
                "row_level": "region",
                "restricted_columns": ["unit_cost"],  # analysts see margin, not unit economics
            },
            "lineage": ["pos_transactions.raw -> daily_aggregation -> gross_margin_pct"],
            "analytical_methods_allowed": ["waterfall_decomposition", "STL_anomaly"],
            "confidence_policy": {"abstain_below": 0.6},
        },
        {
            "kpi_id": "units_sold",
            "name": "Units Sold",
            "formula": "sum(units_sold)",
            "unit": "units",
            "owner": "Head of Operations",
            "dimensions": ["region", "product", "channel"],
            "time_grain": "daily",
            "sources": ["pos_transactions"],
            "freshness_sla_hours": 24,
            "materiality_threshold_pct": 3.0,
            "statistical_threshold_zscore": 2.0,
            "known_drivers": ["price_elasticity", "marketing_spend", "seasonality", "stockouts", "competitor_activity"],
            "controllable_drivers": ["marketing_spend"],
            "access_policy": {"row_level": "region", "restricted_columns": []},
            "lineage": ["pos_transactions.raw -> daily_aggregation -> units_sold"],
            "analytical_methods_allowed": ["STL_anomaly", "DiD_causal_test"],
            "confidence_policy": {"abstain_below": 0.6},
        },
        {
            "kpi_id": "return_rate",
            "name": "Return Rate",
            "formula": "sum(returns_units) / sum(units_sold) * 100",
            "unit": "percent",
            "owner": "Head of Fulfillment",
            "dimensions": ["region", "product"],
            "time_grain": "daily",
            "sources": ["pos_transactions"],
            "freshness_sla_hours": 24,
            "materiality_threshold_pct": 8.0,
            "statistical_threshold_zscore": 2.5,
            "known_drivers": ["product_quality", "logistics_delays", "sizing_issues", "seasonality"],
            "controllable_drivers": ["logistics_performance"],
            "access_policy": {"row_level": "region", "restricted_columns": []},
            "lineage": ["pos_transactions.raw -> daily_aggregation -> return_rate"],
            "analytical_methods_allowed": ["STL_anomaly", "DiD_causal_test"],
            "confidence_policy": {"abstain_below": 0.6},
        },
        {
            "kpi_id": "customer_acquisition_cost",
            "name": "Customer Acquisition Cost",
            "formula": "sum(spend_usd) / sum(new_customers_attributed)",
            "unit": "USD_per_customer",
            "owner": "VP Growth",
            "dimensions": ["region", "channel"],
            "time_grain": "weekly",
            "sources": ["marketing_spend"],
            "freshness_sla_hours": 168,
            "materiality_threshold_pct": 5.0,
            "statistical_threshold_zscore": 2.0,
            "known_drivers": ["marketing_spend_efficiency", "channel_mix", "competitor_cpm_inflation", "creative_fatigue"],
            "controllable_drivers": ["marketing_spend"],
            "access_policy": {"row_level": "region", "restricted_columns": []},
            "lineage": ["marketing_spend.raw -> weekly_rollup -> customer_acquisition_cost"],
            "analytical_methods_allowed": ["STL_anomaly", "DiD_causal_test"],
            "confidence_policy": {"abstain_below": 0.6},
        },
    ],
}


def verify_world(pos: pd.DataFrame, mkt: pd.DataFrame, tix: pd.DataFrame) -> None:
    """Fail loudly if planted effects did not land where designed."""
    pos = pos.copy()
    pos["date"] = pd.to_datetime(pos["date"])
    t_promo_start, t_promo_end = pd.Timestamp(PROMO_START), pd.Timestamp(PROMO_END)
    t_logi_start, t_logi_end = pd.Timestamp(LOGI_START), pd.Timestamp(LOGI_END)
    t_price_start = pd.Timestamp(PRICE_CHANGE_START)

    s = pos[pos.region == "South"]
    s_daily = s.groupby("date").apply(lambda g: (g.units_sold * g.unit_price).sum() - g.returns_value.sum())
    # Weekday-PAIRED check, same construction as the detector: the most
    # recent occurrence of each weekday vs that weekday 3-4 weeks back.
    idx = s_daily.index
    wd = idx.dayofweek
    recent_by_wd = {w: float(s_daily[wd == w].iloc[-1]) for w in set(wd)}
    prior_by_wd = {w: float(s_daily[wd == w].iloc[-5:-3].mean()) for w in set(wd)}
    deltas = [recent_by_wd[w] - prior_by_wd[w] for w in sorted(set(wd))]
    base_level = float(np.mean([prior_by_wd[w] for w in sorted(set(wd))]))
    drop_pct = 100 * float(np.mean(deltas)) / base_level
    assert drop_pct < -3.0, f"E1/E2 revenue dip too weak: {drop_pct:.1f}%"

    pre = pos[pos["date"] < t_promo_start]
    treated_pre = pre[(pre.region == "South") & (pre.product_id.isin(PRICE_TREATED_SKUS))]["unit_price"].unique()
    treated_post = pos[(pos.region == "South") & (pos.product_id.isin(PRICE_TREATED_SKUS))
                       & (pos.date >= t_price_start)]["unit_price"].unique()
    assert set(treated_post) == {round(p * 1.08, 2) for p in treated_pre}, "E2 price not applied"

    w = pos[pos.region == "West"].groupby("date")["units_sold"].sum()
    w_pre, w_logi = w[w.index < t_logi_start].tail(14).mean(), w[(w.index >= t_logi_start) & (w.index <= t_logi_end)].mean()
    assert 100 * (w_logi - w_pre) / w_pre < -4.0, "E3 West dip too weak"

    july_south = tix[(tix.month == "2026-07") & (tix.region == "South")]
    assert july_south.avg_sentiment_score.mean() < -0.3, "E4 sentiment shock missing"

    new_sku_days = pos[pos.product_id == "SKU-NEW-01"]["date"].nunique()
    assert new_sku_days == 21, f"Sparse SKU history wrong: {new_sku_days}"

    south_mkt = mkt[mkt.region == "South"]
    cac = south_mkt.groupby("week_start").apply(lambda g: g.spend_usd.sum() / g.new_customers_attributed.sum())
    surge = cac[cac.index.isin(SURGE_WEEKS)].mean()
    normal = cac[~cac.index.isin(SURGE_WEEKS)].mean()
    assert surge > normal * 1.10, "E1b CAC inflation missing"

    print(f"[verify] South promo-window revenue vs prior 21d avg: {drop_pct:+.1f}%  (target < -4%)")
    print(f"[verify] West logistics window units vs prior 14d avg: {100*(w_logi-w_pre)/w_pre:+.1f}%")
    print(f"[verify] July South sentiment: {july_south.avg_sentiment_score.mean():+.2f}  (target < -0.30)")
    print(f"[verify] Sparse SKU history: {new_sku_days} days  |  CAC surge vs normal: ${surge:.0f} vs ${normal:.0f}")
    print(f"[verify] All planted effects landed.")


SCENARIOS = ["default", "unknown_factor", "adversarial_trap"]


def _last_window(pos: pd.DataFrame, days: int = 14):
    """Return the inclusive [start, end] window over the final `days` of data."""
    end = pd.to_datetime(pos["date"].max())
    start = end - pd.Timedelta(days=days - 1)
    return start, end


def _apply_scenario(scenario: str, pos: pd.DataFrame, mkt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply the active adversarial scenario to finalized dataframes.

    All mutations target the trailing evaluation window (last 14 days) so the
    detector/DiD sees them. Returns (pos, mkt). Deterministic per seed.
    """
    pos = pos.copy()
    mkt = mkt.copy()
    if scenario == "default":
        return pos, mkt

    pad = pd.to_datetime(pos["date"]).copy()  # keep orig dtype for downstream consumers
    pos["date"] = pad
    win_start, win_end = _last_window(pos)
    win_start64, win_end64 = win_start.strftime("%Y-%m-%d"), win_end.strftime("%Y-%m-%d")

    if scenario == "unknown_factor":
        # Unmodelled North sales shock: a -15% dip driven by an unknown factor.
        # Suppress every OTHER planted effect in North during the window so all
        # known drivers (price / marketing / logistics / competitor_promo) stay
        # at baseline and the ONLY movement is the unmodeled shock.
        n_win = (pos["region"] == "North") & (pos["date"] >= win_start)
        pos = pos[~(n_win & (pos["product_id"] == NEW_SKU[0]))]          # kill the launch ramp
        pos.loc[
            (pos["region"] == "North") & (pos["date"] >= win_start),
            "units_sold",
        ] = np.floor(pos.loc[
            (pos["region"] == "North") & (pos["date"] >= win_start),
            "units_sold",
        ] * 0.85).astype(int)
        pos["date"] = pos["date"].dt.strftime("%Y-%m-%d")
        return pos, mkt

    if scenario == "adversarial_trap":
        # Trap: a GLOBAL marketing spend spike (+40%) is a false-positive lure,
        # while the real South net-revenue drop (-10%) is caused by competitor
        # activity - NOT by marketing. Ground truth must credit competitor only.
        mkt_win = (pd.to_datetime(mkt["week_start"]) >= pd.Timestamp(win_start64)) \
            & (pd.to_datetime(mkt["week_start"]) <= pd.Timestamp(win_end64))
        mkt.loc[mkt_win, "spend_usd"] = (mkt.loc[mkt_win, "spend_usd"] * 1.40).round(2)
        mkt.loc[mkt_win, "new_customers_attributed"] = np.maximum(
            1, np.floor(mkt.loc[mkt_win, "new_customers_attributed"] * 0.90).astype(int)
        )
        # South revenue drop: scale units the revenue is built on.
        pos.loc[
            (pos["region"] == "South") & (pos["date"] >= win_start),
            "units_sold",
        ] = np.floor(pos.loc[
            (pos["region"] == "South") & (pos["date"] >= win_start),
            "units_sold",
        ] * 0.90).astype(int)
        pos["date"] = pos["date"].dt.strftime("%Y-%m-%d")
        return pos, mkt

    raise ValueError(f"Unknown scenario: {scenario}")


def main() -> None:
    ap = argparse.ArgumentParser(description="CauseTrace synthetic world generator")
    ap.add_argument("--seed", type=int, default=SEED,
                    help=f"world seed (default {SEED}; the demo world is reproducible per seed)")
    ap.add_argument("--scenario", type=str, default="default", choices=SCENARIOS,
                    help="adversarial benchmark scenario to embed")
    args = ap.parse_args()
    seed = args.seed
    scenario = args.scenario

    DATA_DIR.mkdir(exist_ok=True)
    pos = generate_pos(seed)
    mkt = generate_marketing(seed)

    pos, mkt = _apply_scenario(scenario, pos, mkt)
    tix = generate_tickets(pos, seed)  # regenerated AFTER mutation to stay consistent

    pos.to_csv(DATA_DIR / "pos_transactions.csv", index=False)
    mkt.to_csv(DATA_DIR / "marketing_spend.csv", index=False)
    tix.to_csv(DATA_DIR / "support_tickets.csv", index=False)

    with open(DATA_DIR / "events.json", "w", encoding="utf-8") as f:
        json.dump(build_events_json(scenario), f, indent=2)
    with open(DATA_DIR / "kpi_contracts.json", "w", encoding="utf-8") as f:
        json.dump(KPI_CONTRACTS, f, indent=2)

    print(f"World generated ({START_DATE.date()} .. {END_DATE.date()}), seed={seed}, scenario={scenario}")
    print(f"  pos_transactions : {len(pos):>6} rows x {len(pos.columns)} cols")
    print(f"  marketing_spend  : {len(mkt):>6} rows x {len(mkt.columns)} cols")
    print(f"  support_tickets  : {len(tix):>6} rows x {len(tix.columns)} cols")
    print(f"Set DEMO_NOW={DEMO_NOW_SUGGESTED} in .env for a frozen demo clock.")
    if scenario == "default":
        verify_world(pos, mkt, tix)
    print(f"Data generated successfully for scenario: {scenario}")


if __name__ == "__main__":
    main()
