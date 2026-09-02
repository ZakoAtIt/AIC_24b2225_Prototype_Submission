"""Pipeline stage 5 - causal_engine.

Layered Difference-in-Differences, implemented transparently in pandas/numpy.
No external causal library. For each candidate hypothesis we compare the
treated slice's change (post vs pre) against control slices' change over the
SAME window:

    DiD_effect = (treat_post - treat_pre) - mean(control_post - control_pre)

Why "layered": a single DiD cannot separate two shocks that hit the same
population simultaneously (the week-9... here July price increase AND
competitor promo both affect South). We exploit different comparison slices:

  Test A (price):     treated = price-treated SKUs *inside South*;
                      controls = other South SKUs. The promo hits both sides,
                      so it cancels -> isolates the price effect.
  Test B (promo):     treated = South overall; controls = North & West.
                      Captures total South shock; subtracting Test A's effect
                      isolates the competitor promo.
  Test C (logistics): treated = West; controls = North & South over the
                      logistics window.
  Test D (CAC surge): treated = South weekly CAC; controls = other regions.

Control consistency -> clarity: if control units disagree wildly with each
other, the counterfactual is weak. clarity = clip(1 - CV_of_control_deltas).
Method type logged to telemetry: causal_inference.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as _stats
from statsmodels.regression.linear_model import OLS
from statsmodels.stats.sandwich_covariance import cov_hc1


@dataclass
class DiDResult:
    test_id: str
    hypothesis: str
    treatment: str
    controls: list
    pre_window: tuple[str, str]
    post_window: tuple[str, str]
    treat_delta: float          # in result_unit
    ctrl_deltas: list[float]
    did_effect: float           # in result_unit
    did_effect_pct: float | None
    clarity: float              # 0..1, from control consistency
    verdict: str                # supported | weak | inconclusive
    did_se: float | None = None         # robust (HC1) standard error of did_effect
    did_p_value: float | None = None    # two-sided p-value on did_effect
    did_ci_lo: float | None = None      # 95% CI lower bound
    did_ci_hi: float | None = None      # 95% CI upper bound
    parallel_trends: str = "n/a"        # "pass" | "caution" | "fail"
    parallel_trends_p: float | None = None
    evidence_id: str | None = None      # registry id registered for this DiD test


# Per-test context: the (region, targets) a DiD test actually evaluates. A test
# is admissible as "causal proof" for a KPI card ONLY when BOTH its evaluated
# region and target metric match the card's own region and metric. This blocks
# cross-region leakage (e.g. a West return-rate test being cited to validate a
# North net-revenue drop). Deterministic, no LLM.
DID_TEST_META = {
    "A_price_within_region": {"region": "South", "kpis": {"net_revenue", "units_sold", "gross_margin_pct"}},
    "B_promo_cross_region":  {"region": "South", "kpis": {"net_revenue", "units_sold"}},
    "C_logistics_west":      {"region": "West",  "kpis": {"net_revenue", "units_sold"}},
    "D_returns_west":        {"region": "West",  "kpis": {"return_rate"}},
    "E_cac_south":           {"region": "South", "kpis": {"customer_acquisition_cost"}},
}


def did_relevant(r, kpi_id: str, region: str | None) -> bool:
    """Is a DiD test admissible evidence for the given KPI card context?

    Region matches exactly when a specific region is being evaluated; a regional
    test is NOT treated as causal proof for an ALL_REGIONS (company-wide) card.
    """
    meta = DID_TEST_META.get(r.test_id)
    if meta is None:
        return False
    if kpi_id not in meta["kpis"]:
        return False
    if region and region != "ALL_REGIONS" and meta["region"] != region:
        return False
    if region == "ALL_REGIONS":
        return False
    return True


def did_supported(r) -> bool:
    """Strict causal proof: significant (p<0.05) AND parallel-trends ok."""
    return (
        getattr(r, "verdict", None) == "supported"
        and r.did_p_value is not None
        and r.did_p_value < 0.05
        and getattr(r, "parallel_trends", "n/a") in ("pass", "caution")
    )


def did_expects_did(kpi_id: str) -> bool:
    """Does this KPI lean on DiD as a causal method at all?"""
    return any(kpi_id in m["kpis"] for m in DID_TEST_META.values())


def _did_regression(treat_s: pd.Series, ctrl_s: pd.Series,
                    pre: tuple[str, str], post: tuple[str, str]):
    """Difference-in-differences via OLS with robust (HC1) standard errors.

    Model: y = b0 + b1*post + b2*treated + b3*(post x treated) + e
      b3 is the DiD effect; its HC1 SE yields the p-value and 95% CI.
    Deterministic, transparent, no LLM.
    Returns (effect, se, p_value, ci_lo, ci_hi) in the input series unit.
    """
    pre_s, pre_e = pd.Timestamp(pre[0]), pd.Timestamp(pre[1])
    post_s, post_e = pd.Timestamp(post[0]), pd.Timestamp(post[1])
    t = treat_s.to_frame("y").assign(treated=1)
    c = ctrl_s.to_frame("y").assign(treated=0)
    df = pd.concat([t, c])
    df = df[(df.index >= pre_s) & (df.index <= post_e)]
    df["post"] = df.index.map(lambda d: 1 if post_s <= d <= post_e else 0)
    X = np.column_stack([np.ones(len(df)), df["post"], df["treated"], df["post"] * df["treated"]])
    y = df["y"].values.astype(float)
    if len(y) < 8 or np.linalg.matrix_rank(X) < 4:
        return None
    try:
        model = OLS(y, X).fit(cov_type="HC1")
        b3, se3 = model.params[3], model.bse[3]
        t_stat = b3 / se3 if se3 > 0 else float("nan")
        p = 2.0 * _stats.t.sf(abs(t_stat), df=len(y) - 4)
        crit = _stats.t.ppf(0.975, df=len(y) - 4)
        lo, hi = b3 - crit * se3, b3 + crit * se3
        return float(b3), float(se3), float(p), float(lo), float(hi)
    except Exception:
        return None


def _attach_statistics(result: DiDResult, treat_s: pd.Series, ctrl_s: pd.Series,
                       scale: float = 1.0):
    """Populate SE / p / 95% CI + parallel-trends check on an existing DiDResult.

    The robust DiD regression supplies the sampling SE. CI and p are computed
    AROUND the already-stored point estimate (result.did_effect) so the three
    stay mutually consistent in the displayed unit. `scale` converts the
    regression's raw-unit effect/SE into the result's displayed unit
    (1 for dollars, 100 for percentage points). Deterministic, no LLM.
    """
    reg = _did_regression(treat_s, ctrl_s, result.pre_window, result.post_window)
    dof = None
    se = None
    if reg is not None:
        _eff, se_raw, _p, _lo, _hi = reg
        se = se_raw * scale
        n_pre = int(np.array(len(treat_s[(treat_s.index >= pd.Timestamp(result.pre_window[0])) &
                                         (treat_s.index <= pd.Timestamp(result.pre_window[1]))])))
        dof = max(1, n_pre * 2 - 4)
    else:
        ctrl_arr = np.array(result.ctrl_deltas, dtype=float)
        if len(ctrl_arr) > 1 and np.std(ctrl_arr) > 0:
            se = float(np.std(ctrl_arr, ddof=1) / np.sqrt(len(ctrl_arr)))
            dof = len(ctrl_arr) - 1
    if se is None or se <= 0 or result.did_effect is None:
        label, p = _parallel_trends_test(treat_s, ctrl_s, result.pre_window, result.post_window)
        result.parallel_trends = label
        result.parallel_trends_p = round(p, 4) if p is not None else None
        return
    crit = _stats.t.ppf(0.975, df=dof)
    p = float(2.0 * _stats.t.sf(abs(result.did_effect / se), df=dof))
    result.did_se = round(se, 3)
    result.did_p_value = round(p, 4)
    result.did_ci_lo = round(result.did_effect - crit * se, 2)
    result.did_ci_hi = round(result.did_effect + crit * se, 2)
    label, pt_p = _parallel_trends_test(treat_s, ctrl_s, result.pre_window, result.post_window)
    result.parallel_trends = label
    result.parallel_trends_p = round(pt_p, 4) if pt_p is not None else None


def _parallel_trends_test(treat_s: pd.Series, ctrl_s: pd.Series,
                          pre: tuple[str, str], post: tuple[str, str]):
    """Pre-period placebo: tests whether the treated-vs-control paths were
    already diverging BEFORE the treatment window.

    To be scale-invariant each series is first normalised to its OWN pre-period
    baseline, so differently-sized groups (e.g. a region with a larger revenue
    base) are compared on their RELATIVE trajectories rather than absolute
    levels. A non-zero mean of the normalised pre-period daily differentials
    (relative to its own volatility) implies the parallel-trends assumption is
    questionable - i.e. the treated group was already trending away.

    Returns (label, p_value): label in pass/caution/fail.
    Deterministic t-test, no LLM.
    """
    pre_s, pre_e = pd.Timestamp(pre[0]), pd.Timestamp(pre[1])
    t = treat_s[(treat_s.index >= pre_s) & (treat_s.index <= pre_e)]
    c = ctrl_s[(ctrl_s.index >= pre_s) & (ctrl_s.index <= pre_e)]
    common = t.index.intersection(c.index)
    if len(common) < 6:
        return "n/a", None
    t_vals = t.loc[common].values.astype(float)
    c_vals = c.loc[common].values.astype(float)
    t0, c0 = float(np.mean(t_vals)), float(np.mean(c_vals))
    if t0 <= 0 or c0 <= 0:
        return "n/a", None
    t_norm = t_vals / t0
    c_norm = c_vals / c0
    diff = t_norm - c_norm
    mu, sd = float(np.mean(diff)), float(np.std(diff, ddof=1))
    if sd < 1e-12 or len(common) < 4:
        return "pass", None
    t_stat = mu / (sd / np.sqrt(len(common)))
    p = float(2.0 * _stats.t.sf(abs(t_stat), df=len(common) - 1))
    label = "fail" if p < 0.05 else ("caution" if p < 0.20 else "pass")
    return label, p


def _B_propagated_ci(result: DiDResult, treat_s: pd.Series, ctrl_s: pd.Series, other: DiDResult):
    """Layered Test B: promo effect = gross(South vs North) - price effect (A).

    The two components are separate estimators, so their sampling errors combine
    in quadrature. Confidence interval is placed around the stored layered point. """
    reg = _did_regression(treat_s, ctrl_s, result.pre_window, result.post_window)
    n_pre = int(np.count_nonzero(
        (treat_s.index >= pd.Timestamp(result.pre_window[0])) &
        (treat_s.index <= pd.Timestamp(result.pre_window[1]))))
    dof = max(1, n_pre * 2 - 4)
    se_gross = reg[1] if reg is not None else None
    se_a = other.did_se if other.did_se is not None else None
    if se_gross is None or se_a is None or se_gross <= 0:
        return
    se = float(np.sqrt(se_gross ** 2 + se_a ** 2))
    crit = _stats.t.ppf(0.975, df=dof)
    p = float(2.0 * _stats.t.sf(abs(result.did_effect / se), df=dof))
    result.did_se = round(se, 3)
    result.did_p_value = round(p, 4)
    result.did_ci_lo = round(result.did_effect - crit * se, 2)
    result.did_ci_hi = round(result.did_effect + crit * se, 2)


def _did_relative(treat_pre, treat_post, ctrl_pre_list, ctrl_post_list):
    """Relative-unit DiD: percent changes are compared, then converted back to
    treatment-base dollars. Absolute-dollar DiD across slices of different
    sizes fabricates effects out of pure scale differences."""
    t_delta = treat_post - treat_pre
    t_rel = t_delta / max(abs(treat_pre), 1e-9)
    c_rels = [(cp - cl) / max(abs(cl), 1e-9) for cp, cl in zip(ctrl_post_list, ctrl_pre_list)]
    did_rel = t_rel - float(np.mean(c_rels)) if c_rels else t_rel
    did_dollars = did_rel * abs(treat_pre)
    return t_delta, c_rels, did_rel, did_dollars


def _did_absolute(treat_pre, treat_post, ctrl_pre_list, ctrl_post_list):
    """Same-unit DiD (rates, per-customer dollars): absolute deltas comparable."""
    t_delta = treat_post - treat_pre
    c_deltas = [cp - cl for cp, cl in zip(ctrl_post_list, ctrl_pre_list)]
    did = t_delta - float(np.mean(c_deltas)) if c_deltas else t_delta
    return t_delta, c_deltas, did


def _clarity(ctrl_deltas: list[float], did: float) -> float:
    """Control consistency -> clarity in [0,1].

    Multi-control: how tightly controls agree WITH EACH OTHER around their own
    mean (a parallel-trends proxy). Tight agreement -> high clarity.
    Single-control: no spread observable -> neutral ceiling of 0.55 (honest
    about weaker identification without faking precision).
    No-control: 0.4. Statistical significance itself is NOT re-measured here -
    that is the detector's job - so we do not double-count it.
    """
    if not ctrl_deltas:
        return 0.4
    arr = np.array(ctrl_deltas, dtype=float)
    if len(arr) == 1:
        return 0.55
    mu = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    if abs(mu) < 1e-12:
        return 1.0 if sd < 1e-12 else 0.2   # all-zero vs noisy-zero controls
    cv = sd / abs(mu)
    return round(max(0.0, min(1.0, 1.0 - cv)), 3)


def _daily_series(df: pd.DataFrame, value_col: str) -> pd.Series:
    s = df.groupby("date")[value_col].sum().sort_index()
    s.index = pd.to_datetime(s.index)
    return s


def _window_mean(s: pd.Series, start, end) -> float:
    return float(s[(s.index >= start) & (s.index <= end)].mean())


def run_pos_did_suite(pos_df: pd.DataFrame, eval_days: int = 14, baseline_days: int = 28) -> list[DiDResult]:
    df = pos_df.copy()
    # Exclude the newly launched SKU from EVERY causal slice: its organic ramp
    # would contaminate treatment AND control deltas alike (a level shift that
    # is not one of the tested causes). Launch effects are handled narratively.
    df = df[df["product_id"] != "SKU-NEW-01"]
    df["date"] = pd.to_datetime(df["date"])
    end = pd.Timestamp(df["date"].max())
    post_end, post_start = end, end - pd.Timedelta(days=eval_days - 1)
    pre_end, pre_start = post_start - pd.Timedelta(days=1), post_start - pd.Timedelta(days=baseline_days)

    results: list[DiDResult] = []

    rev = df.assign(rev=df["units_sold"] * df["unit_price"] - df["returns_value"])

    south_treated = ["SKU-101", "SKU-102"]
    st = rev[(rev.region == "South") & (rev.product_id.isin(south_treated))]
    sc = rev[(rev.region == "South") & (~rev.product_id.isin(south_treated))]
    st_s, sc_s = _daily_series(st, "rev"), _daily_series(sc, "rev")
    st_pre = _window_mean(st_s, pre_start, pre_end)
    t_delta, c_rels, did_rel, did_a = _did_relative(
        st_pre, _window_mean(st_s, post_start, post_end),
        [_window_mean(sc_s, pre_start, pre_end)], [_window_mean(sc_s, post_start, post_end)],
    )
    results.append(DiDResult(
        "A_price_within_region", "Price increase on SKU-101/102 (South)",
        "South x treated SKUs", ["South x other SKUs"],
        (str(pre_start.date()), str(pre_end.date())),
        (str(post_start.date()), str(post_end.date())),
        round(t_delta, 2), [round(c, 4) for c in c_rels], round(did_a, 2),
        round(100 * did_rel, 2),
        _clarity(c_rels, did_rel),
        "supported" if abs(did_a) > 0 and _clarity(c_rels, did_rel) > 0.5 else "weak",
    ))
    # attach SE / p / CI + parallel-trends via DiD regression
    result_a = results[-1]
    _attach_statistics(result_a, st_s, sc_s)

    # --- Test B: regional shock (promo dominates) ------------------------------
    # Controls: North ONLY. South carries treatment; West carries its own live
    # logistics event, so using it as control would inject a rival cause.
    reg = {}
    for r in ["North", "South", "West"]:
        reg[r] = _daily_series(rev[rev.region == r], "rev")
    south_pre_b = _window_mean(reg["South"], pre_start, pre_end)
    t_delta_b, c_rels_b, did_rel_b, did_b_gross = _did_relative(
        south_pre_b, _window_mean(reg["South"], post_start, post_end),
        [_window_mean(reg["North"], pre_start, pre_end)],
        [_window_mean(reg["North"], post_start, post_end)],
    )
    promo_effect = did_b_gross - did_a  # layered subtraction isolates the confound
    results.append(DiDResult(
        "B_promo_cross_region",
        f"Competitor promo shock in South (price effect {round(did_a, 1):+} removed by layering)",
        "South region", ["North region"],
        (str(pre_start.date()), str(pre_end.date())),
        (str(post_start.date()), str(post_end.date())),
        round(t_delta_b, 2), [round(c, 4) for c in c_rels_b], round(promo_effect, 2),
        round(100 * promo_effect / max(abs(south_pre_b), 1e-9), 2),
        _clarity(c_rels_b, promo_effect / max(abs(south_pre_b), 1e-9)),
        "supported" if promo_effect < 0 else "weak",
    ))
    result_b = results[-1]
    # Layered estimator: error propagation over the two components.
    # se(promo) = sqrt( se(South-vs-North gross)^2 + se(A price test)^2 )
    _B_propagated_ci(result_b, reg["South"], reg["North"], result_a)
    _pt = _parallel_trends_test(reg["South"], reg["North"], result_b.pre_window, result_b.post_window)
    result_b.parallel_trends = _pt[0]
    result_b.parallel_trends_p = round(_pt[1], 4) if _pt[1] is not None else None

    # --- Test C: logistics delay, West ----------------------------------------
    logi_start, logi_end = post_end - pd.Timedelta(days=13), post_end
    lpre_start, lpre_end = logi_start - pd.Timedelta(days=14), logi_start - pd.Timedelta(days=1)
    w_s = _daily_series(rev[rev.region == "West"], "rev")
    n_s = _daily_series(rev[rev.region == "North"], "rev")
    w_pre_c = _window_mean(w_s, lpre_start, lpre_end)
    t_delta_c, c_rels_c, did_rel_c, did_c = _did_relative(
        w_pre_c, _window_mean(w_s, logi_start, logi_end),
        [_window_mean(n_s, lpre_start, lpre_end)],
        [_window_mean(n_s, logi_start, logi_end)],
    )
    results.append(DiDResult(
        "C_logistics_west", "Logistics delay impact on West revenue",
        "West region", ["North region"],
        (str(lpre_start.date()), str(lpre_end.date())),
        (str(logi_start.date()), str(logi_end.date())),
        round(t_delta_c, 2), [round(c, 4) for c in c_rels_c], round(did_c, 2),
        round(100 * did_rel_c, 2),
        _clarity(c_rels_c, did_rel_c),
        "supported" if did_c < 0 and _clarity(c_rels_c, did_rel_c) > 0.5 else "weak",
    ))
    _attach_statistics(results[-1], w_s, n_s)

    # --- Test D: return rate shift in West (same disruption) -------------------
    rr = df.assign(rr=df["returns_units"] / df["units_sold"].clip(lower=1))
    rr_west = rr[rr.region == "West"].groupby("date")["rr"].mean().sort_index()
    rr_rest = rr[rr.region != "West"].groupby("date")["rr"].mean().sort_index()
    t_delta_d, c_deltas_d, did_d = _did_absolute(
        _window_mean(rr_west, lpre_start, lpre_end), _window_mean(rr_west, logi_start, logi_end),
        [_window_mean(rr_rest, lpre_start, lpre_end)],
        [_window_mean(rr_rest, logi_start, logi_end)],
    )
    base_d = _window_mean(rr_west, lpre_start, lpre_end)
    results.append(DiDResult(
        "D_returns_west", "Logistics delay raising West return rate",
        "West return rate", ["Rest-of-company return rate"],
        (str(lpre_start.date()), str(lpre_end.date())),
        (str(logi_start.date()), str(logi_end.date())),
        round(t_delta_d * 100, 3), [round(c * 100, 3) for c in c_deltas_d],
        round(did_d * 100, 3),
        None,
        _clarity(c_deltas_d, did_d),
        "supported" if did_d > 0 else "weak",
    ))
    _attach_statistics(results[-1], rr_west, rr_rest, scale=100)

    return results


def run_cac_did(mkt_df: pd.DataFrame) -> DiDResult | None:
    mkt = mkt_df.copy()
    mkt["week_start"] = pd.to_datetime(mkt["week_start"])
    g = mkt.groupby(["week_start", "region"]).apply(
        lambda x: x["spend_usd"].sum() / max(x["new_customers_attributed"].sum(), 1)
    ).rename("cac").reset_index()
    weeks = sorted(g["week_start"].unique())
    if len(weeks) < 6:
        return None
    post_w, pre_ws = weeks[-1], weeks[-5:-1]
    south = g[g.region == "South"].set_index("week_start")["cac"]
    others = g[g.region != "South"].groupby("week_start")["cac"].mean()
    t_delta, c_deltas, did = _did_absolute(
        float(south.reindex(pre_ws).mean()), float(south.loc[post_w]),
        [float(others.reindex(pre_ws).mean())], [float(others.loc[post_w])],
    )
    base = float(south.reindex(pre_ws).mean())
    clarity = _clarity(c_deltas, did)
    result_e = DiDResult(
        "E_cac_south", "CAC inflation in South during media surge",
        "South weekly CAC", ["North/West blended CAC"],
        (str(pre_ws[0].date()), str(pre_ws[-1].date())), (str(post_w.date()),) * 2,
        round(t_delta, 2), [round(c, 2) for c in c_deltas], round(did, 2),
        round(100 * did / max(abs(base), 1e-9), 2), clarity,
        "supported" if did > 0 and clarity > 0.5 else "weak",
    )
    _attach_statistics(result_e, south, others)
    return result_e
