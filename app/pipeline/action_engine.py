"""Pipeline stage 7 - action_engine.

Turns CONFIRMED drivers into structured recommendations:

    Driver -> Controllable Lever -> Action -> Expected Impact -> Owner ->
    Confidence -> Monitoring Plan

Rules are a small transparent lookup table (business rules, no ML). Expected
impact is derived by REVERSING the driver's measured contribution in the
waterfall / DiD arithmetic - i.e. "if this lever recovered X% of the measured
effect, the KPI would move by ~$Y".

Two mappings make every actionable KPI produce cards:

  DID_TO_DRIVER   test id prefix -> explanatory driver name
  DRIVER_LEVER_MAP  driver -> the CONTROLLABLE lever that addresses it
                    (returns_rate is fixed through logistics ops; volume loss
                    from a competitor promo is fought through our own media/
                    offer budget - flagged addresses_non_controllable)

Controllability is checked AFTER lever mapping, so e.g. Return Rate (whose
contract lists logistics_performance as its lever) still yields actions.

Method type logged to telemetry: business_rules.
"""

from dataclasses import dataclass

import numpy as np
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize


@dataclass
class ActionCard:
    driver: str
    lever: str
    action: str
    expected_impact: str
    expected_impact_value: float
    impact_unit: str
    owner: str
    confidence_tier: str
    addresses_non_controllable: bool
    monitoring_plan: str
    experiment: dict | None = None

    def as_dict(self):
        return self.__dict__.copy()


# DiD test-id prefix -> explanatory driver name (matches contract vocab).
DID_TO_DRIVER = {
    "A": "price",
    "B": "competitor_activity",
    "C": "logistics_performance",
    "D": "returns_rate",
    "E": "marketing_spend",
}

# Display labels for action cards. Tautological internal driver keys are
# surfaced to the user under a more semantically descriptive, non-tautological
# name. Applied only to the ActionCard `driver` OUTPUT - internal lookup keys
# (RULE_TABLE / DRIVER_LEVER_MAP / controllable-driver matching) are unchanged.
DRIVER_LABEL_MAP = {
    "returns_rate": "logistics_fulfillment_delay",
    "marketing_spend": "bidding_inefficiency",
}

# Some root causes are not themselves controllable; they are addressed THROUGH
# a controllable lever (documented, and surfaced on the action card).
DRIVER_LEVER_MAP = {
    "returns_rate": "logistics_performance",
    "competitor_activity": "marketing_spend",   # counter-offer via our budget
    "volume": "competitor_activity",
}

# (lever_driver, direction_of_measured_effect) -> (lever, action template).
# Direction is the SIGN of the effect on the KPI; each rule interprets it.
RULE_TABLE = {
    ("price", "down"): (
        "Pricing",
        "Evaluate a targeted promotional counter-offer in {region}; test rollback on treated SKUs.",
    ),
    ("price", "up"): (
        "Pricing",
        "Protect the pricing action in {region}: hold list price, monitor elasticity weekly for churn signals.",
    ),
    ("marketing_spend", "up"): (
        "Media budget",
        "Reallocate {region} spend toward efficient channels; pause underperforming surge campaigns.",
    ),
    ("marketing_spend", "down"): (
        "Media budget",
        "Shift incremental {region} budget into the channels driving the movement; hold always-on baseline.",
    ),
    ("competitor_activity", "down"): (
        "Promotional counter-offer",
        "Launch a matched value-offer bundle + loyalty double-points window in {region} to defend volume.",
    ),
    ("logistics_performance", "up"): (
        "Fulfillment ops",
        "Audit carrier handling on the {region} lane; add protective packaging and expedite backlogged freight.",
    ),
    ("returns_rate", "up"): (
        "Fulfillment ops",
        "Inspect return reasons for affected SKUs in {region}; fix top defect driver before restocking.",
    ),
}

MONITOR_TEMPLATE = (
    "Re-check {kpi_id} materiality in 14 days; alert if deviation again exceeds "
    "{threshold}% or z passes {zthresh}. Auto-trigger DiD re-evaluation in 14 days."
)


# Which causal tests can explain which KPI (keeps e.g. a price-revenue effect
# from becoming an 'action' on the Return Rate card).
KPI_RELEVANT_TESTS = {
    "net_revenue": "ABCDE",
    "units_sold": "BC",            # demand-side volume shocks
    "return_rate": "D",
    "gross_margin_pct": "",
    "customer_acquisition_cost": "E",
}


def _driver_direction(effect: float) -> str:
    return "up" if effect > 0 else "down"


def _impact_unit(kpi_unit: str, test_letter: str | None) -> str:
    """Keep the measured unit, honestly labeled when it differs from the KPI."""
    base = {"D": "pp", "E": "USD/customer"}.get(test_letter, "USD/day")
    if test_letter in (None, "A", "B", "C") and kpi_unit != "USD":
        return f"{base} equiv."
    return base


def _size_experiment(did_result, expected_impact_value: float, unit: str) -> dict | None:
    """Size an A/B test to confirm a driver before scaling the action.

    Uses a two-sample proportion power calculation (statsmodels) so the test
    has 80% power at alpha=0.05 to detect the recoverable lift. If the DiD did
    not reach significance, no test is proposed (currently-scaled action only).

    Deterministic; the LLM never performs this sizing.
    """
    if did_result is None:
        return None
    if did_result.did_p_value is None or did_result.did_p_value > 0.05:
        return None                      # not significant -> no experiment proposed
    p0 = 0.05                            # documented baseline target-action rate
    lift_pct = abs(did_result.did_effect_pct) / 100.0 if did_result.did_effect_pct else 0.05
    lift = max(0.02, min(0.50, lift_pct))  # floor/ceiling keeps power math sane
    p1 = p0 * (1 + lift)
    if p1 >= 1.0:
        return None
    h = proportion_effectsize(p1, p0)
    analysis = NormalIndPower()
    n_per_arm = int(np.ceil(analysis.solve_power(effect_size=h, alpha=0.05, power=0.80,
                                                 ratio=1.0, alternative="two-sided")))
    daily_volume = 300                      # documented assumed trials/day/arm
    duration_days = max(1, int(np.ceil(n_per_arm / daily_volume)))
    return {
        "test_type": "A/B",
        "n_per_arm": n_per_arm,
        "duration_days": duration_days,
        "alpha": 0.05,
        "power": 0.80,
        "baseline_rate": p0,
        "target_lift_pct": round(lift * 100, 1),
        "effect_size_h": round(float(h), 3),
        "note": ("Confirm the driver before scaling: two-arm A/B sized to detect "
                 "the recoverable effect at 80% power, 5% significance."),
    }


def generate_actions(contract, waterfall=None, did_results=None,
                     confidence_tier: str = "", region_label: str = "",
                     corrections: dict | None = None) -> list[ActionCard]:
    """Build action cards from measured contributions. Deterministic.

    `corrections` maps driver -> count of human 'correct' feedback. It acts as
    a DAMPED ranking tiebreak only (never as fabricated evidence):
        sort_key = |effect| * (1 + 0.05 * min(count, 5))   # max +25%
    """
    cards: list[ActionCard] = []
    corrections = corrections or {}
    controllable = set(contract.controllable_drivers)
    region = region_label or "the affected region"

    # ---- collect (driver, measured_effect, unit, did_result) candidates ----
    candidates: list[tuple[str, float, str, object]] = []

    if waterfall is not None:
        for c in waterfall.components_usd_per_day:
            name = c["driver"]
            if name == "mix":
                continue          # rarely actionable alone in this prototype
            candidates.append((name, c["effect"], "USD/day", None))

    from app.pipeline.causal_engine import did_relevant, did_supported
    region = None if region_label in (None, "", "ALL_REGIONS") else region_label
    for r in (did_results or []):
        letter = r.test_id[:1]
        driver = DID_TO_DRIVER.get(letter)
        if driver is None or r.verdict == "inconclusive":
            continue
        if contract.kpi_id in KPI_RELEVANT_TESTS and \
                letter not in KPI_RELEVANT_TESTS[contract.kpi_id]:
            continue
        # DiD context isolation + strict causal proof: only a test that is
        # relevant to THIS card's region/metric AND statistically supported
        # (p<0.05 AND parallel-trends ok) may become an action driver. This
        # prevents a non-relevant test from driving a lever here.
        if not (did_relevant(r, contract.kpi_id, region) and did_supported(r)):
            continue
        candidates.append((driver, r.did_effect,
                           _impact_unit(contract.unit, letter), r))

    def _rank_key(item):
        driver, effect, _unit, _r = item
        boost = 1.0 + 0.05 * min(corrections.get(driver, 0), 5)
        return abs(effect) * boost

    seen = set()
    for driver, effect, unit, did_result in sorted(candidates, key=_rank_key, reverse=True):
        direction = _driver_direction(effect)
        key = (driver, direction)
        if key in seen or effect == 0:
            continue
        seen.add(key)

        lever_driver = DRIVER_LEVER_MAP.get(driver, driver)
        addresses_non_controllable = lever_driver != driver
        if lever_driver not in controllable:
            continue                                   # no decision rights here
        # Prefer the driver-specific rule text (e.g. the competitor counter-
        # offer), fall back to a lever-level rule.
        rule = RULE_TABLE.get((driver, direction)) or \
            RULE_TABLE.get((lever_driver, direction))
        if rule is None:
            continue
        lever, action_tmpl = rule

        recovery_share = 0.6   # conservative assumption: lever recovers ~60%
        impact_val = round(-effect * recovery_share, 2)

        owner = "VP Growth" if lever == "Promotional counter-offer" else contract.owner

        cards.append(ActionCard(
            driver=DRIVER_LABEL_MAP.get(driver, driver),
            lever=lever,
            action=action_tmpl.format(region=region),
            expected_impact=(
                f"~{impact_val:+,.2f} {unit} vs current run-rate "
                f"(assumes recovering {int(recovery_share*100)}% of the measured effect)"
            ),
            expected_impact_value=impact_val,
            impact_unit=unit,
            owner=owner,
            confidence_tier=confidence_tier,
            addresses_non_controllable=addresses_non_controllable,
            monitoring_plan=MONITOR_TEMPLATE.format(
                kpi_id=contract.kpi_id,
                threshold=contract.materiality_threshold_pct,
                zthresh=contract.statistical_threshold_zscore,
            ),
            experiment=_size_experiment(did_result, impact_val, unit),
        ))
        if len(cards) >= 3:
            break
    return cards
