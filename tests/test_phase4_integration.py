"""Phase 4 integration gate: full-stack smoke test via TestClient.

Covers: overview ranking, CFO analysis, analyst RBAC (200 + 403),
evidence endpoint, feedback weight nudge, telemetry rows, events firewall
labeling, and the abstention path on the contradiction KPI.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("DEMO_NOW", "2026-07-30")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# --- health -----------------------------------------------------------------
r = client.get("/health")
check("health 200", r.status_code == 200)

# Clear stale DuckDB caches so narrative cache hit tests are deterministic
from app.services.store import get_conn as _gc
_gc().execute("DELETE FROM narrative_cache")

# --- overview ----------------------------------------------------------------
r = client.get("/kpis")
check("overview 200", r.status_code == 200)
cards = r.json()["kpis"]
check("overview has 5 kpis", len(cards) == 5, str(len(cards)))
check("overview sorted by severity desc",
      all(cards[i]["severity"] >= cards[i + 1]["severity"] for i in range(len(cards) - 1)))
nr = next(c for c in cards if c["kpi_id"] == "net_revenue")
check("net_revenue flagged adverse in South", nr["material"] and nr["scope"] == "South"
      and nr["status"] == "adverse", f"{nr}")

# --- CFO analysis (happy path) -------------------------------------------------
r = client.get("/kpis/net_revenue/analysis", params={"persona": "cfo", "role": "cfo"})
check("cfo analysis 200", r.status_code == 200, r.text[:300])
body = r.json()
check("cfo view shape", body.get("persona") == "cfo" and "headline" in body)
check("narrative validated", body["narrative_meta"]["validated"] is True,
      str(body["narrative_meta"]))
check("no violations", not body["narrative_meta"].get("violations"))
check("has actions", "recommended_action" in body)
check("request id header", bool(r.headers.get("x-request-id")))

# second identical call -> narrative cache hit
r2 = client.get("/kpis/net_revenue/analysis", params={"persona": "cfo", "role": "cfo"})
check("cache hit on repeat", r2.json()["narrative_meta"]["cache_hit"] is True)

# --- Analyst RBAC ---------------------------------------------------------------
r = client.get("/kpis/net_revenue/analysis",
               params={"persona": "analyst", "role": "analyst", "user_region": "West"})
check("analyst own-region 200", r.status_code == 200, r.text[:300])
awest = r.json()
check("analyst sees own region", awest["movement"]["scope"] == "West")
check("west logistics flagged material down",
      awest["movement"]["material"] and awest["movement"]["direction"] == "down",
      f"{awest['movement']}")
check("analyst causal controls redacted",
      all(t["controls"] == ["[restricted]"] for t in awest.get("causal_tests", [])))

# --- DiD uncertainty + experiment sizing (SOTA-keeper fields) -------------------
rdi = client.get("/kpis/net_revenue/analysis",
                 params={"persona": "analyst", "role": "analyst", "user_region": "South"})
asouth = rdi.json()
_causal = asouth.get("causal_tests", [])
check("DiD SE/p/CI populated on every test",
      all(t.get("did_se") is not None and t.get("did_p_value") is not None
          and t.get("did_ci_lo") is not None and t.get("did_ci_hi") is not None
          for t in _causal), str(_causal)[:400])
check("DiD p-value in valid range",
      all(0 < t["did_p_value"] < 1 for t in _causal))
check("DiD CI bounds the point estimate",
      all(t["did_ci_lo"] <= t["did_effect"] <= t["did_ci_hi"] for t in _causal if t["did_effect"] is not None))
check("parallel-trends verdict present",
      any(t.get("parallel_trends") in ("pass", "caution", "fail") for t in _causal))
# A/B sizing appears only where a STATISTICALLY SIGNIFICANT driver drives an
# action. net_revenue South has no significant causal test, so no experiment
# (correct abstention); the return_rate KPI (significant D-test) does.
rrt = client.get("/kpis/return_rate/analysis",
                 params={"persona": "analyst", "role": "analyst", "user_region": "West"})
rrt_actions = rrt.json().get("actions", [])
_exp_rrt = [a for a in rrt_actions if a.get("experiment")]
check("significant driver produces A/B sizing",
      len(_exp_rrt) >= 1,
      str([a.get("experiment") for a in rrt_actions])[:400])
check("A/B sizing has sane numbers",
      all(e["n_per_arm"] > 0 and e["duration_days"] > 0 and e["power"] == 0.80
          for a in _exp_rrt for e in [a["experiment"]]))
check("A/B sizing absent without significance",
      all(not a.get("experiment") for a in asouth.get("actions", [])))
r = client.get("/kpis/net_revenue/analysis",
               params={"persona": "analyst", "role": "analyst",
                       "user_region": "West", "focus_region": "South"})
check("analyst cross-region 403", r.status_code == 403, str(r.status_code))

r = client.get("/kpis/gross_margin_pct/analysis",
               params={"persona": "analyst", "role": "analyst", "user_region": "South"})
check("south margin analysis 200", r.status_code == 200, r.text[:300])
margin = r.json()

# --- Abstention path -------------------------------------------------------------
# gross_margin_pct South: margin UP while sentiment DOWN -> Contradictory.
conf = margin["confidence"]
check("contradiction -> abstain", conf.get("abstain") is True, str(conf)[:300])
cq = margin.get("clarifying_question")
check("clarifying question present", bool(cq))
check("tier is Contradictory", conf.get("tier") == "Contradictory")


# --- Evidence endpoint --------------------------------------------------------------
r = client.get("/kpis/net_revenue/evidence")
check("evidence 200", r.status_code == 200)
ev = r.json()["evidence"]
check("evidence non-empty with ids/methods",
      len(ev) > 5 and all(e.get("evidence_id") and e.get("method_type") for e in ev))

# --- Feedback loop -------------------------------------------------------------------
# reset live weights for determinism across runs
from app.services.store import get_conn as _get_conn
_get_conn().execute("DELETE FROM confidence_weights")

r0 = client.get("/telemetry", params={"summary": True})
before = r0.json()["summary"]
r = client.post("/feedback", json={"insight_id": "net_revenue:South:cfo",
                                   "action": "accept", "kpi_id": "net_revenue"})
check("feedback 200", r.status_code == 200, r.text[:200])
fb = r.json()
check("weights moved & renormalized",
      abs(sum(fb["weights_after"].values()) - 1.0) < 1e-6
      and fb["weights_after"] != fb["weights_before"], str(fb))

r = client.post("/feedback", json={"insight_id": "x", "action": "bogus"})
check("bad action 422", r.status_code == 422)

# --- Telemetry ------------------------------------------------------------------------
r = client.get("/telemetry", params={"limit": 10})
check("telemetry 200", r.status_code == 200)
reqs = r.json()["requests"]
check("telemetry rows exist", len(reqs) >= 3)
sample = next(r_ for r_ in reqs if r_["route"].endswith("/analysis"))
check("telemetry has stages+llm fields",
      isinstance(sample["stages"], list) and "llm_calls" in sample and "est_cost_usd" in sample)
stage_types = {s["method_type"] for s in sample["stages"]}
check("analysis trace spans statistics+LLM+rules",
      {"statistics", "LLM", "business_rules", "deterministic_arithmetic"} <= stage_types,
      str(stage_types))
print("    analysis stage method types:", sorted(stage_types))

after = client.get("/telemetry", params={"summary": True}).json()["summary"]
check("session totals accumulate", after["requests"] > before["requests"])
check("telemetry economics: p50/p95/cache-rate/tokens-per-insight",
      all(k in after and after[k] is not None
          for k in ("p50_latency_ms", "p95_latency_ms", "cache_hit_rate",
                    "tokens_per_insight")), str(after))
print(f"    session totals: {after}")

# --- Events firewall endpoint -----------------------------------------------------------
# --- Phase 8: delivery channel -------------------------------------------------
check("cfo delivery_channel present", "delivery_channel" in body
      and body["delivery_channel"]["channel"] in ("email_digest", "dashboard"),
      str(body.get("delivery_channel")))

# --- Phase 8: audit trail --------------------------------------------------------
ra = client.get("/kpis/net_revenue/analysis", params={"persona": "analyst",
                                                      "role": "analyst", "user_region": "West"})
rid = ra.headers.get("x-request-id")
r = client.get(f"/audit/{rid}")
check("audit 200", r.status_code == 200, r.text[:200])
audit = r.json()
check("audit joins telemetry+evidence",
      audit["request"]["request_id"] == rid and len(audit["evidence"]) > 3,
      str(len(audit.get("evidence", []))))
r = client.get("/audit/nonexistent")
check("audit unknown 404", r.status_code == 404)

# --- Phase 8: evaluation harness ---------------------------------------------------
r = client.get("/evaluation")
check("evaluation 200", r.status_code == 200, r.text[:200])
ev = r.json()["detector_scorecard"]
print(f"    detector: precision={ev['precision']} recall={ev['recall']} "
      f"confusion={ev['confusion']}")
check("detector precision >= 0.8", ev["precision"] >= 0.8, str(ev["precision"]))
check("detector recall >= 0.9", ev["recall"] >= 0.9, str(ev["recall"]))
check("drift report flags sentiment shock",
      any(d["slice"].startswith("support_sentiment") and d["psi"] > 0.5
          for d in r.json()["drift"]))

# --- Phase 8: correction memory ------------------------------------------------------
client.post("/feedback", json={"insight_id": "net_revenue:South:cfo", "action": "correct",
                               "corrected_driver": "competitor_activity", "kpi_id": "net_revenue"})
corr = client.get("/feedback/history").json()["corrections"]
check("correction counted", corr.get("competitor_activity", 0) >= 1, str(corr))

# --- Phase 8/9: every material KPI carries actions + narration validates ------
for k, focus in [("net_revenue", "South"), ("return_rate", "West"),
                 ("units_sold", "South"), ("customer_acquisition_cost", "South")]:
    r = client.get(f"/kpis/{k}/analysis",
                   params={"persona": "cfo", "role": "cfo", "focus_region": focus})
    b = r.json()
    check(f"{k}: narration validated (no false positives)",
          b["narrative_meta"]["validated"] is True,
          str(b["narrative_meta"].get("violations"))[:200])
    check(f"{k}: recommended action present",
          len(b.get("actions", [])) >= 1, str(len(b.get("actions", []))))
    check(f"{k}: direction-aware wording",
          ("up" in b["headline"].lower() or "down" in b["headline"].lower())
          and (k == "net_revenue" or "net revenue" not in b["headline"].lower()),
          b["headline"])

# --- Phase 9: validator still catches hallucinations ---------------------------
from app.pipeline.llm_narrative import validate_narrative
from app.pipeline.evidence_registry import EvidenceRegistry as _ER
_er = _ER()
_er.add(claim="known dev", value=-6.98, unit="percent")
_ok, _v = validate_narrative({"summary": "moved by -6.98% [EV-0001]"}, _er)
check("validator accepts traced numbers incl EV ids", _ok, str(_v))
_ok, _v = validate_narrative({"summary": "moved by -9.13% for no reason"}, _er)
check("validator rejects invented numbers", not _ok)

# --- Phase 9: plain-English correction integration -----------------------------
r = client.post("/feedback", json={
    "insight_id": "net_revenue:South:cfo", "action": "correct",
    "correction_text": "the competitor discount in South took our volume",
    "kpi_id": "net_revenue"})
check("plain-english correction 200", r.status_code == 200, r.text[:300])
integ = r.json().get("integration", {})
check("intent mapped to competitor_activity",
      integ.get("understood_as") == "competitor_activity", str(integ))
check("receipt lists integration points", len(integ.get("integrated_into", [])) >= 2)

corr = client.get("/feedback/history").json()["corrections"]
check("normalized driver counted in correction memory",
      corr.get("competitor_activity", 0) >= 1, str(corr))

# --- Recovery simulation (counterfactual) -----------------------------------
r = client.get("/kpis/net_revenue/recovery-sim")
check("recovery-sim 200", r.status_code == 200, r.text[:300])
sim = r.json()
check("sim labeled as simulation", sim["simulation"] is True and "SIMULATION" in sim["warning"])
regions_hit = [r_ for r_ in sim["per_region"] if r_["was_adversely_down"]]
print("    adversely-down regions:", [(r_["region"], r_["before"]["pct_deviation"],
                                       "->", r_["after"]["pct_deviation"]) for r_ in regions_hit])
print("    all regions:", [(r_["region"], round(r_["avg_daily_net_revenue_in_window"]["delta_usd_per_day"]))
                           for r_ in sim["per_region"]])
check("South and West were adversely down", {"South", "West"} <= {r_["region"] for r_ in regions_hit})
check("sim recovers materiality in every affected region", sim["recovers_materiality"] is True)
check("recovery adds daily revenue everywhere affected",
      all(r_["avg_daily_net_revenue_in_window"]["delta_usd_per_day"] > 0 for r_ in regions_hit))

r = client.get("/kpis/net_revenue/recovery-sim",
               params={"role": "analyst", "user_region": "West", "focus_region": "South"})
check("recovery-sim RBAC 403", r.status_code == 403)

# --- Ground-truth firewall -----------------------------------------------------
import subprocess
grep = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-ChildItem -Recurse app\\pipeline -Filter *.py | "
     "Select-String -Pattern 'events\\.json|events_path|load_events' | Measure-Object | "
     "% Select-Object -ExpandProperty Count"],
    capture_output=True, text=True)
count = int(grep.stdout.strip() or "0")
check("firewall: no events.json references in app/pipeline", count == 0, str(count))

r = client.get("/events")
check("events 200 with warning label", r.status_code == 200
      and "DEBUG" in r.json().get("warning", ""))

# --- CAC analysis (weekly grain + E-test driver) ------------------------------------------
r = client.get("/kpis/customer_acquisition_cost/analysis",
               params={"persona": "analyst", "role": "analyst", "user_region": "South"})
check("cac south 200", r.status_code == 200, r.text[:300])
cac = r.json()
check("cac movement uses weekly fallback",
      cac["movement"]["method_used"] == "rolling_zscore_fallback",
      cac["movement"]["method_used"])

# --- units_sold / return_rate quick pass ----------------------------------------------------
for k in ("units_sold", "return_rate"):
    r = client.get(f"/kpis/{k}/analysis", params={"role": "cfo"})
    check(f"{k} cfo 200", r.status_code == 200, r.text[:200])

# --- Cross-source evidence (3 distinct sources in net_revenue South) ----------------------
# Use /analysis with focus_region=South to get marketing context + sentiment evidence
r_cs = client.get("/kpis/net_revenue/analysis",
                  params={"persona": "cfo", "role": "cfo", "focus_region": "South"})
check("focused analysis 200", r_cs.status_code == 200)
cs_body = r_cs.json()
mv = cs_body.get("movement") or {}
check("top-level movement in response (fixes subtitle/traffic-light)",
      isinstance(mv, dict) and "direction" in mv and "pct_deviation" in mv and "z_score" in mv,
      str(cs_body.get("movement"))[:200])
# Check evidence via the /evidence endpoint (analysis response doesn't include raw evidence list)
r_ev = client.get("/kpis/net_revenue/evidence")
ev_items = r_ev.json()["evidence"]
sources = {e["source"] for e in ev_items}
check("net_revenue cites 3+ distinct sources",
      len(sources & {"pos_transactions", "marketing_spend", "support_tickets"}) >= 3,
      f"sources: {sources}")

ticket_evs = [e for e in ev_items if e["source"] == "support_tickets"]
check("sentiment evidence registered",
      len(ticket_evs) >= 1 and any("sentiment" in e.get("claim", "") for e in ticket_evs),
      str(len(ticket_evs)))

pval_evs = [e for e in ev_items if e.get("unit") == "p_value"]
check("p-value evidence present (scipy)", len(pval_evs) >= 1, str(len(pval_evs)))

# --- alternative_hypotheses & cross_source_context ------------------------------------------
check("alternative_hypotheses in response",
      isinstance(cs_body.get("alternative_hypotheses"), list))
check("cross_source_context in response",
      isinstance(cs_body.get("cross_source_context"), list) and len(cs_body["cross_source_context"]) >= 1,
      str(cs_body.get("cross_source_context", []))[:300])

# --- Overview TTL cache (second call warm) ---------------------------------------------------
import time
t0 = time.perf_counter()
r = client.get("/kpis")
warm_ms = (time.perf_counter() - t0) * 1000
check("overview warm call < 500ms (TTL cache)", warm_ms < 500, f"{warm_ms:.0f}ms")

# --- Watch status appears on at least one card ----------------------------------------------
statuses = {c["status"] for c in r.json()["kpis"]}
check("traffic lights include watch/watch/favorable",
      "watch" in statuses or "favorable" in statuses, str(statuses))

print()
if failures:
    print(f"FAILURES ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL PHASE 4 INTEGRATION CHECKS PASSED")
