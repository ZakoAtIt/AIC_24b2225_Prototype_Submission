"""Pipeline stage 3 - detection_engine.

Flags *material* KPI movements using two gates that BOTH must pass:
  1. statistical significance : z-score of recent residual vs rolling baseline
  2. business materiality     : % deviation exceeds the KPI contract threshold

Primary method: STL decomposition (statsmodels, weekly seasonality period=7).
Residuals after removing trend+seasonality are compared against a robust
sigma estimated EXCLUDING the evaluation window (so the anomaly cannot
inflate its own baseline).

Fallback method (honest degradation): series shorter than 4 seasonal cycles
(e.g. SKU-NEW-01's 21 days of history) use a trailing rolling-baseline
z-score instead, and the result is labeled as such everywhere the method is
shown. Weekly-grain KPIs (CAC) also use the fallback - too few points for STL.

Method type logged to telemetry: statistics.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL


@dataclass
class DetectionResult:
    kpi_id: str
    scope: str                       # region or ALL_REGIONS
    material: bool
    direction: str                   # up | down | none
    z_score: float
    pct_deviation: float             # signed, vs expected level
    method_used: str                 # STL_anomaly | rolling_zscore_fallback
    threshold_z: float
    threshold_pct: float
    eval_window_days: int
    series: dict = field(default_factory=dict)   # chart payload

    @property
    def passed_statistical(self) -> bool:
        return abs(self.z_score) >= self.threshold_z

    @property
    def passed_materiality(self) -> bool:
        return abs(self.pct_deviation) >= self.threshold_pct


MIN_STL_POINTS = 28          # >= 4 weekly cycles
EVAL_WINDOW = 7              # evaluate trailing week of residuals
SIGMA_EXCLUDE_LAST = 14      # days excluded from sigma estimation at the end


def _odd(x: int) -> int:
    return int(x) if int(x) % 2 == 1 else int(x) - 1


def _stl_detection(s: pd.Series, threshold_z: float, threshold_pct: float,
                   kpi_id: str, scope: str) -> DetectionResult:
    """Hybrid detector (transparent by design):

    * STL (statsmodels) removes weekly seasonality -> supplies the chart's
      expected-level band and the residual sigma used for the band width.
    * The statistical gates use a WEEKDAY-PAIRED comparison: each weekday in
      the evaluation window is compared against its own prior-two-week mean,
      so calendar mix can never masquerade as (or mask) a movement. A guard
      band keeps the reference window clear of any plausible recent onset.

    Both gates read their thresholds from the KPI contract at runtime.
    """
    vals = s.values.astype(float)
    trend_window = _odd(max(51, min(len(vals) - 2, 99)))
    stl = STL(vals, period=7, trend=trend_window, robust=True).fit()
    resid = stl.resid

    W = EVAL_WINDOW          # trailing week under evaluation
    idx = s.index
    wd = np.asarray(idx.dayofweek)

    deltas, priors = [], []
    for w in sorted(set(wd.tolist())):
        vals_w = vals[wd == w]
        if len(vals_w) < 6:
            continue
        recent_w = float(np.mean(vals_w[-1:]))        # most recent occurrence
        # Reference = the SAME weekday 3-4 weeks back. Two-week-old events
        # otherwise sit inside their own baseline and self-dilute.
        prior_w = float(np.mean(vals_w[-5:-3]))
        deltas.append(recent_w - prior_w)
        priors.append(prior_w)
    if len(deltas) < 5:
        return _rolling_detection(s, threshold_z, threshold_pct, kpi_id, scope, window=W)

    d_arr = np.array(deltas, dtype=float)
    se = max(float(np.std(d_arr, ddof=1)) / np.sqrt(len(d_arr)), 1e-9)
    z = float(np.mean(d_arr)) / se
    base_level = max(abs(float(np.mean(priors))), 1e-9)
    pct = float(np.mean(d_arr)) / base_level * 100.0

    material = abs(z) >= threshold_z and abs(pct) >= threshold_pct
    direction = "down" if pct < 0 else ("up" if material else "none")

    sigma_resid = max(1.4826 * float(np.median(np.abs(resid[:-SIGMA_EXCLUDE_LAST]
                                                   - np.median(resid[:-SIGMA_EXCLUDE_LAST])))), 1e-9)
    band = (stl.trend + stl.seasonal)
    series = {
        "period": [str(ts.date()) for ts in s.index],
        "actual": [round(float(v), 2) for v in vals],
        "baseline": [round(float(v), 2) for v in band],
        "band_low": [round(float(v - 1.96 * sigma_resid), 2) for v in band],
        "band_high": [round(float(v + 1.96 * sigma_resid), 2) for v in band],
        "eval_window_start": str(idx[-W].date()),
    }
    return DetectionResult(kpi_id, scope, bool(material), direction, round(z, 2),
                           round(float(pct), 2), "STL_anomaly", threshold_z, threshold_pct,
                           W, series)


def _rolling_detection(s: pd.Series, threshold_z: float, threshold_pct: float,
                       kpi_id: str, scope: str, window: int = 7) -> DetectionResult:
    """Trailing-baseline z-score for short histories / coarse grains."""
    vals = s.values.astype(float)
    if len(vals) <= window + 5:
        window = max(3, len(vals) // 3)
    baseline = vals[:-window]
    mu, sd = float(np.mean(baseline)), float(np.std(baseline, ddof=1))
    sd = max(sd, 1e-9)
    recent = float(np.mean(vals[-window:]))
    se = sd / np.sqrt(window)
    z = (recent - mu) / se
    pct = (recent - mu) / max(abs(mu), 1e-9) * 100.0
    material = abs(z) >= threshold_z and abs(pct) >= threshold_pct
    direction = "down" if pct < 0 else ("up" if material else "none")

    # flat pseudo-baseline band for charts
    series = {
        "period": [str(ts.date()) for ts in s.index],
        "actual": [round(float(v), 2) for v in vals],
        "baseline": [round(mu, 2)] * len(vals),
        "band_low": [round(mu - 1.96 * sd, 2)] * len(vals),
        "band_high": [round(mu + 1.96 * sd, 2)] * len(vals),
        "eval_window_start": str(s.index[-window].date()),
        "note": "Short history - rolling baseline used (STL needs 4+ seasonal cycles)",
    }
    return DetectionResult(kpi_id, scope, material, direction, round(z, 2),
                           round(pct, 2), "rolling_zscore_fallback",
                           threshold_z, threshold_pct, window, series)


def detect_movement(frame: pd.DataFrame, contract, scope: str = "ALL") -> DetectionResult:
    """frame: tidy KPI frame already filtered to scope; aggregates over remaining dims."""
    grain_col = "period"
    agg = frame.groupby(grain_col, as_index=False)["value"].sum() \
        if contract.kpi_id != "gross_margin_pct" else _recompute_margin(frame)
    s = agg.set_index(grain_col)["value"].sort_index()
    if len(s) < 10:
        return DetectionResult(contract.kpi_id, scope, False, "none", 0.0, 0.0,
                               "insufficient_history", contract.statistical_threshold_zscore,
                               contract.materiality_threshold_pct, len(s))

    if contract.kpi_id == "customer_acquisition_cost" or len(s) < MIN_STL_POINTS:
        return _rolling_detection(s, contract.statistical_threshold_zscore,
                                  contract.materiality_threshold_pct, contract.kpi_id, scope)
    return _stl_detection(s, contract.statistical_threshold_zscore,
                          contract.materiality_threshold_pct, contract.kpi_id, scope)


def _recompute_margin(frame: pd.DataFrame) -> pd.DataFrame:
    """Margin is a ratio - summing ratios across dims is wrong. Recompute from parts."""
    g = frame.groupby("period", as_index=True).agg(
        net=("net", "sum"), margin_usd=("margin_usd", "sum"), returns_value=("returns_value", "sum")
    )
    g["value"] = (g["margin_usd"] - g["returns_value"]) / g["net"] * 100.0
    return g.reset_index()


def detect_per_region(frame: pd.DataFrame, contract) -> list[DetectionResult]:
    """Detect independently per region so the overview can rank severity."""
    results = []
    for region, sub in frame.groupby("region"):
        results.append(detect_movement(sub, contract, scope=region))
    return results
