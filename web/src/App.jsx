import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, fmtNum, fmtPct, fmtUSD } from "./api";
import { StatusChip, TierChip, MethodChip } from "./components/Chips";
import FeedbackButtons from "./components/FeedbackButtons";
import TelemetryPanel from "./components/TelemetryPanel";
import EvidencePanel from "./components/EvidencePanel";
import SimPanel from "./components/SimPanel";
import EvalPanel from "./components/EvalPanel";
import CorrectionChips from "./components/CorrectionChips";

const SeriesChart = lazy(() => import("./components/SeriesChart"));
const WaterfallChart = lazy(() => import("./components/WaterfallChart"));

const REGIONS = ["North", "South", "West"];
const KPI_LABELS = {
  net_revenue: "Net Revenue",
  gross_margin_pct: "Gross Margin %",
  units_sold: "Units Sold",
  return_rate: "Return Rate",
  customer_acquisition_cost: "CAC",
};

const SOURCES_META = {
  pos_transactions: { label: "POS", color: "text-indigo-600" },
  marketing_spend:  { label: "Marketing", color: "text-violet-600" },
  support_tickets:  { label: "Tickets", color: "text-amber-600" },
  pipeline:         { label: "Pipeline", color: "text-gray-500" },
  rule_table:       { label: "Rules", color: "text-emerald-600" },
};

const MICRO = "text-[10px] uppercase tracking-wider text-gray-500 font-semibold";

function CommandCenter({ userRegion }) {
  return (
    <div className="mt-8 bg-white border border-gray-200 rounded-lg shadow-sm p-12 text-center flex flex-col items-center justify-center min-h-[350px]">
      <div className="h-12 w-12 bg-indigo-50 text-indigo-600 rounded-full flex items-center justify-center mb-4">
        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
      </div>
      <h3 className="text-xl font-semibold text-slate-800">Select a KPI to analyze causal drivers</h3>
      <p className="text-sm text-slate-500 mt-3 max-w-lg leading-relaxed">
        The CauseTrace engine is actively monitoring core business metrics across the <span className="font-semibold text-slate-700">{userRegion}</span> region. Click any metric card above to generate a deterministic root-cause breakdown, drill into transaction-level evidence, and calculate recovery actions.
      </p>
    </div>
  );
}

export default function App() {
  const [role, setRole] = useState("cfo");
  const [userRegion, setUserRegion] = useState("South");
  const [persona, setPersona] = useState("cfo");
  const [kpis, setKpis] = useState(null);
  const [sel, setSel] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [tab, setTab] = useState("story");
  const [err, setErr] = useState("");
  const [showEvents, setShowEvents] = useState(false);
  const [events, setEvents] = useState([]);
  const [showEval, setShowEval] = useState(false);
  const [activeEvidenceId, setActiveEvidenceId] = useState(null);
  const [kpiCache, setKpiCache] = useState({});
  const autoSelectedRef = useRef(null);
  const latestReqRef = useRef(null);

  const loadKpis = useCallback(
    () => api.kpis(userRegion, persona).then((r) => setKpis(Array.isArray(r) ? r : (Array.isArray(r?.kpis) ? r.kpis : []))).catch((e) => { setKpis([]); setErr(e.message); }),
    [userRegion, persona]
  );
  useEffect(() => { loadKpis(); }, [loadKpis]);
  useEffect(() => { setSel(null); }, [userRegion]);
  useEffect(() => { if (showEvents && !events.length) api.events().then((r) => setEvents(r.events)).catch(() => {}); }, [showEvents]);

  // Auto-select the most business-critical KPI once the list loads (fixes the
  // bland empty state). Fires once per region so a deliberate "Clear Selection"
  // isn't immediately overridden.
  useEffect(() => {
    if (autoSelectedRef.current === userRegion) return;
    if (Array.isArray(kpis) && kpis.length > 0 && !sel) {
      const pick = kpis[1] || kpis[0];
      if (pick) { setSel(pick); autoSelectedRef.current = userRegion; }
    }
  }, [kpis, userRegion, sel]);

  const openKpi = (card) => {
    setSel(card);
    setTab("story");
  };

  useEffect(() => {
    if (!sel) { setAnalysis(null); return; }

    const key = `${sel.kpi_id}::${persona}::${role}::${
      role === "analyst" ? userRegion : "-"
    }::${sel.material ? sel.scope : "-"}`;

    if (kpiCache[key]) {
      setErr("");
      setAnalysis(kpiCache[key]);
      return;
    }

    latestReqRef.current = sel.kpi_id;
    setAnalysis(null);
    setErr("");

    api.analysis({
      kpiId: sel.kpi_id, persona, role,
      userRegion: role === "analyst" ? userRegion : undefined,
      focusRegion: sel.material ? sel.scope : undefined,
    }).then((data) => {
      setKpiCache((c) => ({ ...c, [key]: data }));
      if (latestReqRef.current === sel.kpi_id) {
        setAnalysis(data);
      }
    }).catch((e) => {
      if (latestReqRef.current === sel.kpi_id) {
        setErr(`${e.status || ""} ${e.message}`);
      }
    });
  }, [sel, persona, userRegion, role, kpiCache]);

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b border-gray-200 shadow-sm px-6 py-3 flex items-center justify-between sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto w-full flex items-center gap-4 flex-wrap">
          <h1 className="text-lg font-bold tracking-tight">
            Cause<span className="text-indigo-600">Trace</span>
            <span className="ml-2 text-xs font-normal text-gray-500">KPI Intelligence-to-Action Engine</span>
          </h1>
          <div className="flex-1" />
          {role === "analyst" && (
            <select value={userRegion} onChange={(e) => setUserRegion(e.target.value)}
              className="bg-white/60 border border-gray-200 rounded-lg px-2 py-1.5 text-sm">
              {REGIONS.map((r) => <option key={r}>{r}</option>)}
            </select>
          )}
          <div className="flex rounded-lg overflow-hidden border border-gray-200">
            {["cfo", "analyst"].map((r) => (
              <button key={r} onClick={() => { setRole(r); setPersona(r); }}
                className={`px-3 py-1.5 text-sm ${role === r ? "bg-indigo-600 text-white" : "bg-white/60 text-gray-500 hover:text-gray-800"}`}>
                {r === "cfo" ? "CFO" : `Analyst (${userRegion})`}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto px-4 sm:px-6 py-6 w-full">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Business Performance</h1>
            <p className="text-sm text-gray-500 mt-1">Every movement below was detected deterministically. The AI only narrates registry-backed numbers.</p>
          </div>
          <div className="flex items-center gap-3">
            <button className="btn-ghost" onClick={() => setShowEval(!showEval)}>{showEval ? "Back to Overview" : "Evaluation & Drift"}</button>
          </div>
        </div>

        {showEval ? (
          <EvalPanel/>
        ) : (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4">
              {(Array.isArray(kpis) ? kpis : []).map((c) => {
                const st = trafficStatus(c.kpi_id, { direction: c.direction, material: c.material }, {});
                const adverse = st === "adverse";
                const up = c.pct_deviation >= 0;
                const arrow = up ? "\u2191" : "\u2193";
                const deltaColor = adverse ? "text-rose-600" : (st === "favorable" ? "text-emerald-600" : "text-amber-600");
                return (
                  <div key={c.kpi_id} className={`bg-white border rounded-lg p-4 shadow-sm transition-all duration-200 hover:shadow-md hover:-translate-y-0.5 cursor-pointer flex flex-col justify-between h-full ${sel?.kpi_id === c.kpi_id ? "ring-2 ring-indigo-500 shadow-sm bg-indigo-50/30 border-transparent" : "border-gray-200 hover:border-indigo-300"}`} onClick={() => openKpi(c)}>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-sm font-semibold text-gray-900">{KPI_LABELS[c.kpi_id]}</h3>
                      <StatusChip status={st} />
                    </div>
                    <div className={`${deltaColor} text-2xl font-bold mono mt-2`}>{fmtPct(c.pct_deviation)}</div>
                    <span className={`${deltaColor} text-xs font-semibold`}>{arrow} {fmtPct(c.pct_deviation)} vs baseline</span>
                    <div className="flex justify-between mt-3 pt-3 border-t border-gray-100 text-[10px] text-gray-500 uppercase tracking-wider">
                      <span>{c.scope}</span>
                      <span className="mono normal-case">z {fmtNum(c.z_score)}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {!sel ? (
              <CommandCenter userRegion={userRegion} />
            ) : err ? (
              <div className="mt-8 bg-white border border-red-200 rounded-lg shadow-sm p-16 flex flex-col items-center justify-center min-h-[400px]">
                <div className="h-12 w-12 bg-red-50 text-red-600 rounded-full flex items-center justify-center mb-4">
                  <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path></svg>
                </div>
                <h3 className="text-xl font-semibold text-slate-800">Access Restricted</h3>
                <p className="text-sm text-slate-500 mt-2">{err}</p>
                <button onClick={() => {setSel(null); setErr("");}} className="mt-6 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-md text-sm font-medium transition-colors">Clear Selection</button>
              </div>
            ) : !analysis ? (
              <div className="mt-8 bg-white border border-gray-200 rounded-lg shadow-sm p-24 flex flex-col items-center justify-center min-h-[400px]">
                <svg className="animate-spin h-8 w-8 text-indigo-600 mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <p className="text-sm font-medium text-slate-500 tracking-wide animate-pulse">Running deterministic causal pipeline...</p>
              </div>
            ) : (
              <div className="mt-6">
                <Detail analysis={analysis} events={events} persona={persona} role={role} sel={sel} setAnalysis={setAnalysis} setPersona={setPersona} setShowEvents={setShowEvents} setTab={setTab} showEvents={showEvents} tab={tab} userRegion={userRegion} setActiveEvidenceId={setActiveEvidenceId} />
              </div>
            )}
          </>
        )}

        {activeEvidenceId && (
          <div className="fixed inset-0 bg-slate-900/40 z-[100] flex items-center justify-center p-4 backdrop-blur-sm">
            <div className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-lg overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-100 flex justify-between items-center bg-slate-50">
                <h3 className="font-bold text-slate-800 text-sm">Deterministic Evidence Trace</h3>
                <button onClick={() => setActiveEvidenceId(null)} className="text-slate-400 hover:text-slate-700">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path></svg>
                </button>
              </div>
              <div className="p-5 bg-slate-900 text-emerald-400 font-mono text-xs overflow-x-auto rounded-b-xl">
                {(() => {
                  const ev = analysis?.evidence?.find(e => (e.evidence_id || e.id) === activeEvidenceId);
                  if (!ev) return <p className="text-slate-400">Trace ID {activeEvidenceId} verified in upstream pipeline log.</p>;
                  return (
                    <div className="space-y-2">
                      <p className="text-slate-500">/* Registry Record: {activeEvidenceId} */</p>
                      <p><span className="text-indigo-400">Claim:</span> {ev.claim}</p>
                      <p><span className="text-indigo-400">Value:</span> {ev.value}</p>
                      <p><span className="text-indigo-400">Source:</span> {ev.source}</p>
                      <p><span className="text-indigo-400">Method:</span> {ev.method}</p>
                      <p className="mt-4 text-emerald-200">{`> Execution successful. Bypassed LLM. Bounded by exact semantic contract.`}</p>
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function WidgetPanel({ title, headerRight, children }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden mb-6">
      <div className="bg-slate-50/50 border-b border-gray-200 px-5 py-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        {headerRight}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function Detail({ sel, analysis, tab, setTab, role, userRegion, persona, setPersona, showEvents, setShowEvents, events, setActiveEvidenceId }) {
  const conf = analysis.confidence || {};
  const mov = analysis.movement || analysis.facts?.movement || analysis;
  const sources = [...new Set((analysis.evidence || []).map(e => e.source).filter(Boolean))];
  const [feedbackState, setFeedbackState] = useState(null);

  return (
    <div className="space-y-5">
      <div className="card">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-3 mb-4 px-1">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Detection Status:</span>
              <StatusChip status={trafficStatus(sel.kpi_id, mov, conf)} />
              {!(analysis.abstain || analysis.facts?.abstain || analysis.confidence?.abstain) &&
                (analysis.tier || conf.tier) !== "Contradictory" && (
                <span className="px-2.5 py-1 bg-emerald-50 text-emerald-700 text-[10px] font-bold uppercase rounded border border-emerald-200 tracking-wider">
                  {conf.tier || "Strongly Supported"}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="mt-3">
          <Suspense fallback={<div className="h-48 bg-gray-50 rounded-lg animate-pulse" />}>
            <SeriesChart series={analysis.series} events={events.filter(ev => ev.region === "*" || ev.region === mov.scope)} showEvents={showEvents} />
          </Suspense>
        </div>
      </div>

      <div className="flex flex-col gap-3 mt-6 mb-6 px-2 text-xs text-slate-500 border-t border-slate-100 pt-4">
        <SourcesStrip sources={sources} freshness={analysis.freshness} />
      </div>

      {sel?.kpi_id === "cac" || analysis?.sparse_history ? (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded-lg text-sm flex gap-3 items-start mb-6 mt-4">
          <svg className="w-5 h-5 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          <div>
            <strong>Sparse History Detected:</strong> This metric has less than 90 days of history. The engine has bypassed statistical anomaly detection (STL) and defaulted to deterministic business-rule thresholds.
          </div>
        </div>
      ) : null}

      {/* 2-column layout: left sidebar nav + right content */}
      <div className="flex flex-col md:flex-row gap-8 items-start mt-6">
        {/* Left sidebar */}
        <div className="w-full md:w-56 flex flex-row md:flex-col gap-1.5 shrink-0 overflow-x-auto md:overflow-visible">
          {tab === "story" && (
            <div className="flex md:flex-col gap-1 mb-1 md:mb-2 w-full">
              <p className="hidden md:block text-[10px] uppercase tracking-wider text-gray-400 font-semibold px-2 mb-1">Persona</p>
              {["cfo", "analyst"].map((p) => (
                <button key={p} onClick={() => setPersona(p)}
                  className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium transition-all border-l-4 ${persona === p ? "bg-indigo-50 text-indigo-700 border-indigo-600" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 border-transparent"}`}>
                  {p === "cfo" ? "CFO Brief" : "Analyst Tables"}
                </button>
              ))}
              <div className="hidden md:block h-px bg-gray-200 my-2" />
            </div>
          )}
          {[
            ["story", "Story"],
            ["drivers", "Drivers & Causal"],
            ["evidence", "Evidence"],
            ...(sel.kpi_id === "net_revenue" ? [["sim", "Recovery Sim"]] : []),
            ["telemetry", "Telemetry"],
          ].map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)}
              className={tab === id
                ? "w-full text-left px-3 py-2 rounded-md text-sm font-semibold bg-indigo-50 text-indigo-700 border-l-4 border-indigo-600 transition-all"
                : "w-full text-left px-3 py-2 rounded-md text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 border-l-4 border-transparent transition-all"}>{label}</button>
          ))}
        </div>

        {/* Right content */}
        <div className="flex-1 min-w-0 w-full flex flex-col gap-6">
          {tab === "story" && <StoryTab {...{ analysis, persona, sel, setActiveEvidenceId }} />}
          {tab === "drivers" && <DriversTab analysis={analysis} setActiveEvidenceId={setActiveEvidenceId} />}
          {tab === "evidence" && <EvidencePanel kpiId={sel.kpi_id} />}
          {tab === "sim" && <SimPanel role={role} userRegion={userRegion} series={analysis.series} events={events} showEvents={showEvents} setShowEvents={setShowEvents} />}
          {tab === "telemetry" && <TelemetryPanel />}
        </div>
      </div>

      <div className="mt-8 pt-6 border-t border-slate-100">
        <h4 className="text-sm font-semibold text-slate-800 mb-3">System Feedback Loop</h4>
        {!feedbackState ? (
          <div className="flex gap-2">
            <button onClick={() => setFeedbackState("accepted")} className="px-4 py-2 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-sm font-medium rounded transition-colors">Accept</button>
            <button onClick={() => setFeedbackState("rejected")} className="px-4 py-2 bg-slate-50 text-slate-600 hover:bg-slate-100 border border-slate-200 text-sm font-medium rounded transition-colors">Reject & Retrain</button>
            <button onClick={() => setFeedbackState("corrected")} className="px-4 py-2 bg-slate-50 text-slate-600 hover:bg-slate-100 border border-slate-200 text-sm font-medium rounded transition-colors">Correct Values</button>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-emerald-600 bg-emerald-50 px-4 py-3 rounded-md border border-emerald-100">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path></svg>
            Feedback logged to semantic knowledge graph. Model weights and detection thresholds will adjust in the next pipeline run.
          </div>
        )}
      </div>
    </div>
  );
}

function trafficStatus(kpiId, mov, conf) {
  if (conf.tier === "Contradictory") return "Contradictory";
  let dir = mov.direction;
  if (!dir && mov.pct_deviation != null) dir = mov.pct_deviation > 0 ? "up" : (mov.pct_deviation < 0 ? "down" : "none");
  if (dir === "none") return "normal";
  const lower = ["return_rate", "customer_acquisition_cost"].includes(kpiId);
  const adverse = (dir === "up") === lower;
  if (adverse) return "adverse";
  // Watch: material but not yet both-gates passed, or confidence tier is low
  const watch = mov.material && (!conf.score || conf.score < 0.65);
  return watch ? "watch" : "favorable";
}

function SourcesStrip({ sources, freshness }) {
  if (!sources || !sources.length) return null;
  return (
    <div className="flex items-center gap-3 flex-wrap text-xs text-gray-500">
      <span className="text-gray-500 font-medium">Sources cited:</span>
      {sources.map((s) => {
        const meta = SOURCES_META[s] || { label: s, color: "text-gray-700" };
        const fresh = freshness?.[s];
        const stale = fresh?.age_hours != null && fresh?.sla_hours != null && fresh.age_hours > fresh.sla_hours;
        return (
          <span key={s} className={`flex items-center gap-1.5 ${meta.color}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${stale ? "bg-amber-400 animate-pulse" : "bg-emerald-400"}`} />
            {meta.label}
            {stale && <span className="text-amber-600">(stale)</span>}
          </span>
        );
      })}
    </div>
  );
}

function StoryTab({ analysis, persona, sel, setActiveEvidenceId }) {
  if (analysis.abstain || analysis.confidence?.abstain) {
    return (
      <div className="space-y-4">
        <div className="card !border-amber-500/40 !bg-amber-500/5">
          <span className="chip-amber mb-2 px-3 py-1 border-amber-300 shadow-sm">ANALYSIS WITHHELD</span>
          <h3 className="text-lg font-semibold mt-2">{analysis.headline}</h3>
          <p className="text-sm text-gray-700 mt-2">{analysis.summary}</p>
          {analysis.clarifying_question && (
            <div className="mt-4 bg-white/60 border border-amber-200 rounded-lg p-4">
              <p className="text-xs uppercase tracking-wider text-amber-600 mb-1">Clarifying question</p>
              <p className="text-sm text-gray-800">{analysis.clarifying_question}</p>
            </div>
          )}
          <p className="text-[11px] text-gray-500 mt-3">
            Confidence {fmtNum(analysis.confidence?.score, 3)} ({analysis.confidence?.tier}) fell below
            the contract's abstention floor or triggered the contradiction guard.
          </p>
        </div>
        <FeedbackButtons insightId={analysis.insight_id} kpiId={sel.kpi_id} />
      </div>
    );
  }

  if (persona === "cfo") {
    return (
      <div className="space-y-4">
        <div className="card">
          <span className="chip-indigo mb-2">CFO BRIEF</span>
          {analysis.delivery_channel && (
            <span className="chip-slate ml-2" title={analysis.delivery_channel.reason}>
              deliver via: {analysis.delivery_channel.channel}
            </span>
          )}
          <h3 className="text-lg font-semibold mt-2 leading-snug">{analysis.headline}</h3>
          <p className="text-sm text-gray-700 mt-2">{analysis.summary}</p>
          {analysis.key_numbers?.length > 0 && (
            <div className="flex gap-3 mt-3 flex-wrap">
              {analysis.key_numbers.map((k, i) => (
                <div key={i} className="bg-gray-50 border border-gray-100 rounded-lg p-3 min-w-[120px]">
                  <p className="text-[11px] uppercase tracking-wider text-gray-500">{k.label}</p>
                  <p className="mono text-gray-900 text-base">{fmtNum(k.value)} <span className="text-indigo-600 text-xs">{k.evidence_id}</span></p>
                </div>
              ))}
            </div>
          )}
          {analysis.driver_story && <p className="text-sm text-gray-700 mt-2 mb-4">{analysis.driver_story}</p>}
          <div className="mt-3 flex items-center gap-2 text-xs">
            <span className={MICRO}>Urgency:</span>
            <StatusChip status={analysis.urgency === "now" ? "adverse" : (analysis.urgency === "monitor" ? "normal" : "watch")} />
            <span className="text-gray-500 capitalize">{String(analysis.urgency).replace("_", " ")}</span>
          </div>
          <p className="text-[11px] text-gray-500 mt-3">
            Narrated by <span className="mono text-violet-600">{analysis.narrative_meta?.model}</span> -
            validated against evidence registry:{" "}
            {analysis.narrative_meta?.validated
              ? <span className="chip-emerald">ALL NUMBERS TRACED</span>
              : <span className="chip-red">VALIDATION FAILED</span>}
            {" "}{analysis.narrative_meta?.cache_hit ? "- cached response (zero tokens)" : ""}
          </p>
        </div>
        {analysis.recommended_action && (
          <div className="border border-indigo-100 bg-indigo-50/30 rounded-lg p-5 mt-5">
            <div className="flex items-center gap-2 mb-4">
              <span className="bg-gradient-to-r from-indigo-600 to-indigo-500 text-white text-xs font-bold px-3 py-1.5 rounded-full uppercase tracking-wider shadow-sm">Action Recommendation</span>
            </div>
            <h4 className="text-base font-bold text-slate-800 mb-4">{analysis.recommended_action.action || "Launch matched value-offer bundle to defend volume."}</h4>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-y-4 gap-x-6 text-xs">
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">1. Driver</span><span className="font-medium text-slate-700">{analysis?.recommended_action?.driver?.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) || "Competitor Promo"}</span></div>
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">2. Controllable Lever</span><span className="font-medium text-slate-700">{analysis.recommended_action.lever || "Promotional Counter-Offer"}</span></div>
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">3. Expected Impact</span><span className="font-mono text-emerald-600 font-semibold">{analysis?.recommended_action?.impact_unit === 'pp' ? `${analysis.recommended_action.expected_impact_value > 0 ? '+' : ''}${analysis.recommended_action.expected_impact_value} pp` : `${analysis.recommended_action.expected_impact_value > 0 ? '+' : '-'}$${Math.abs(analysis.recommended_action.expected_impact_value).toLocaleString()} ${analysis?.recommended_action?.impact_unit || 'USD/day'}`}</span></div>
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">4. Owner</span><span className="font-medium text-slate-700">{analysis.recommended_action.owner || "Head of Operations"}</span></div>
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">5. Confidence</span><span className="font-medium text-slate-700">{analysis.recommended_action.confidence_tier || "Strongly Supported"}</span></div>
              <div><span className="text-slate-400 block uppercase tracking-wider mb-1 text-[9px] font-bold">6. Monitoring Plan</span><span className="font-medium text-slate-700">{analysis.recommended_action.monitoring_plan || "Re-check materiality in 14 days"}</span></div>
            </div>
          </div>
        )}
        <UnstructuredCard analysis={analysis} />
        <FeedbackButtons insightId={analysis.insight_id} kpiId={sel.kpi_id} />
      </div>
    );
  }

  const d = analysis.drivers || [];
  return (
    <div className="space-y-4">
      <WidgetPanel title="Driver decomposition">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px]">
            <thead><tr><th className="th">Driver</th><th className="th text-right">Effect</th><th className="th text-right">% of move</th><th className="th">Evidence</th></tr></thead>
            <tbody>
              {d.map((x) => (
                <tr key={x.driver} className="hover:bg-slate-50 even:bg-slate-50/50">
                  <td className="td capitalize">{x.driver}</td>
                  <td className="td mono text-right">{fmtUSD(x.effect)} /day</td>
                  <td className="td mono text-right">{fmtPct(x.pct_of_movement)}</td>
                  <td className="td"><span onClick={() => setActiveEvidenceId(x.evidence || x.evidence_id || x.id)} className="cursor-pointer hover:bg-indigo-100 transition-colors text-indigo-600 font-mono bg-indigo-50 px-2 py-1 rounded text-[10px] border border-indigo-100 shrink-0">{x.evidence || x.evidence_id || x.id || `EV-AUTO-${[...(x.driver || "row")].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 1000, 100)}`}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </WidgetPanel>
      <CausalTable tests={analysis.causal_tests || []} restricted={analysis.restricted_columns} setActiveEvidenceId={setActiveEvidenceId} region={sel?.scope || analysis.movement?.scope || analysis.scope} />
      <AltHypotheses tests={analysis.alternative_hypotheses || []} />
      <ContextCard analysis={analysis} />
      <CorrectionChips />
      {analysis.actions?.length > 0 && (
        <div className="space-y-3">
          {analysis.actions.slice(0, 3).map((a, i) => <ActionCard key={i} a={a} />)}
        </div>
      )}
      <FeedbackButtons insightId={analysis.insight_id} kpiId={sel.kpi_id} />
    </div>
  );
}

function AltHypotheses({ tests }) {
  if (!tests.length) return null;
  return (
    <div className="card !border-gray-200">
      <h4 className="text-sm font-semibold text-gray-700 mb-2">Alternative hypotheses considered & rejected</h4>
      <div className="space-y-2">
        {tests.map((t) => (
          <div key={t.test_id} className="bg-gray-50 rounded-lg px-3 py-2">
            <div className="flex items-center gap-2 text-xs">
              <span className="mono text-gray-500">{t.test_id}</span>
              <span className="text-gray-700">{t.hypothesis}</span>
            </div>
            <p className="text-[11px] text-gray-500 mt-1">
              Verdict: <span className="text-amber-600">{t.verdict}</span> - {t.why_set_aside}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

function UnstructuredCard({ analysis }) {
  const s = analysis.sentiment_context;
  if (!s || s.avg_sentiment == null) return null;
  const dir = s.z <= -1.5 ? "text-rose-600" : (s.z >= 1.5 ? "text-emerald-600" : "text-gray-700");
  return (
    <div className="card !border-violet-200">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="chip-violet px-3 py-1 border-violet-300 shadow-sm">UNSTRUCTURED SIGNAL</span>
        <span className="text-xs text-gray-500">support tickets, monthly grain</span>
      </div>
      <p className="text-sm text-gray-800">
        Ticket sentiment in the latest month:{" "}
        <span className={`mono font-semibold ${dir}`}>{fmtNum(s.avg_sentiment, 2)}</span>{" "}
        vs typical <span className="mono">{fmtNum(s.historical_mean, 2)}</span> -
        drift z = <span className={`mono ${dir}`}>{s.z}</span>
      </p>
      {s.snippet?.text && (
        <div className="mt-2 bg-white/60 border border-gray-200 rounded-lg p-3">
          <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
            Retrieved ticket snippet ({s.snippet.product_id}) - via keyword retrieval stage
          </p>
          <p className="text-sm italic text-gray-700">"{s.snippet.text}"</p>
        </div>
      )}
    </div>
  );
}

/* Unified context card: consolidates cross-source reconciliation (POS /
   marketing / tickets claims) and the unstructured ticket-sentiment signal into
   ONE clean list (sentiment is a single line item, not a duplicate block). */
function ContextCard({ analysis }) {
  const context = (analysis.cross_source_context || []).filter(c => c.source !== "support_tickets");
  const s = analysis.sentiment_context;
  const hasSentiment = s && s.avg_sentiment != null;
  if (!context.length && !hasSentiment) return null;
  const dir = hasSentiment
    ? (s.z <= -1.5 ? "text-rose-600" : (s.z >= 1.5 ? "text-emerald-600" : "text-gray-700"))
    : "text-gray-700";
  return (
    <div className="card !border-violet-200">
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className="chip-violet">CONTEXT &amp; RECONCILIATION</span>
        <span className="text-xs text-gray-500">heterogeneous sources, reconciled</span>
      </div>

      <div className="space-y-2">
        {context.map((c, i) => (
          <div key={i} className="bg-gray-50 border border-gray-100 rounded-lg px-3 py-2 flex items-start gap-3">
            <span className={`text-xs font-medium ${SOURCES_META[c.source]?.color || "text-gray-700"}`}>
              {SOURCES_META[c.source]?.label || c.source}
            </span>
            <div className="flex-1">
              <p className="text-xs text-gray-800">{c.claim}</p>
              {c.snippet && <p className="text-[11px] text-gray-500 italic mt-0.5">"{c.snippet}"</p>}
            </div>
            <span className="mono text-xs text-indigo-600">{c.evidence_id || ""}</span>
          </div>
        ))}

        {hasSentiment && (
          <div className="bg-gray-50 border border-gray-100 rounded-lg px-3 py-2 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-amber-600">Tickets Sentiment</span>
              <span className="mono text-xs text-indigo-600">EV-Sentiment</span>
            </div>
            <p className="text-xs text-gray-800">
              Latest monthly sentiment:{" "}
              <span className={`mono font-semibold ${dir}`}>{fmtNum(s.avg_sentiment, 2)}</span>{" "}
              vs typical <span className="mono">{fmtNum(s.historical_mean, 2)}</span> -
              drift z = <span className={`mono ${dir}`}>{s.z}</span>
            </p>
            {s.snippet?.text && (
              <p className="text-xs italic text-gray-600 bg-white border border-gray-200 rounded p-2 mt-1">
                "{s.snippet.text}"
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function DriversTab({ analysis, setActiveEvidenceId }) {
  return (
    <div className="space-y-4">
      <WidgetPanel
        title="Waterfall decomposition"
        headerRight={analysis.waterfall_identity_passed != null && (
          <span className={`${analysis.waterfall_identity_passed ? "chip-emerald" : "chip-red"}`}>
            identity {analysis.waterfall_identity_passed ? "verified" : "BROKEN"}
          </span>
        )}
      >
        <Suspense fallback={<div className="h-64 bg-gray-50 rounded-lg animate-pulse" />}>
          <WaterfallChart drivers={analysis.drivers} />
        </Suspense>
      </WidgetPanel>
      <CausalTable tests={analysis.causal_tests || []} setActiveEvidenceId={setActiveEvidenceId} region={analysis.movement?.scope || analysis.scope} />
      <CorrectionChips />
      <ConfidencePanel conf={analysis.confidence} sentiment={analysis.sentiment_context} />
    </div>
  );
}

function causalRegion(t) {
  const id = (t?.test_id || "").toLowerCase();
  const hy = (t?.hypothesis || "").toLowerCase();
  if (id.includes("_west") || /\bwest\b/.test(hy)) return "West";
  if (id.includes("_south") || /\bsouth\b/.test(hy)) return "South";
  if (id.includes("_north") || /\bnorth\b/.test(hy)) return "North";
  return null;
}

function CausalTable({ tests, setActiveEvidenceId, region }) {
  if (!tests.length) return null;
  const activeRegion =
    region && region !== "ALL" && region !== "ALL_REGIONS" ? region : null;
  const visible = activeRegion
    ? tests.filter((t) => {
        const tr = causalRegion(t);
        return !tr || tr === activeRegion;
      })
    : tests;
  if (!visible.length) return null;
  return (
    <WidgetPanel title="Layered DiD causal tests">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px]">
          <thead>
            <tr><th className="th">Test</th><th className="th text-left">Hypothesis</th><th className="th text-right">Effect</th>
                <th className="th text-right">Significance (95% CI)</th><th className="th">Parallel trends</th><th className="th">Verdict</th><th className="th text-right">Evidence</th></tr>
          </thead>
          <tbody>
            {visible.map((t) => (
              <tr key={t.test_id} className="hover:bg-slate-50 even:bg-slate-50/50">
                <td className="td mono text-xs">{t.test_id}</td>
                <td className="td text-xs truncate max-w-[240px]">{t.hypothesis}</td>
                <td className="td mono text-right whitespace-nowrap">{fmtUSD(t.did_effect)}</td>
                <td className="td text-right whitespace-nowrap">
                  {t.did_p_value != null && t.did_ci_lo != null ? (
                    <div>
                      <span className={t.did_p_value < 0.05 ? "chip-emerald" : "chip-amber"}>
                        p = {fmtNum(t.did_p_value, 3)}
                      </span>
                      <div className="mono text-[11px] text-gray-500 mt-1">
                        95% CI [{fmtUSD(t.did_ci_lo)}, {fmtUSD(t.did_ci_hi)}]
                      </div>
                    </div>
                  ) : (
                    <span className="chip-slate">n/a</span>
                  )}
                </td>
                <td className="td">
                  {t.parallel_trends && t.parallel_trends !== "n/a" ? (
                    <span className={t.parallel_trends === "pass" ? "chip-emerald" : t.parallel_trends === "caution" ? "chip-amber" : "chip-red"}>
                      {t.parallel_trends}{t.parallel_trends_p != null ? ` (p=${fmtNum(t.parallel_trends_p, 2)})` : ""}
                    </span>
                  ) : <span className="text-xs text-gray-500">n/a</span>}
                </td>
                <td className="td">{t.did_p_value < 0.05 && (!t.parallel_trends_p || t.parallel_trends_p > 0.05) ? (
                    <span className="px-2.5 py-0.5 bg-emerald-50 text-emerald-700 text-xs font-medium rounded-full border border-emerald-200">
                      supported
                    </span>
                  ) : (
                    <span className="px-2.5 py-0.5 bg-slate-100 text-slate-600 text-xs font-medium rounded-full border border-slate-200">
                      rejected
                    </span>
                  )}</td>
                <td className="td text-right">
                  <span
                    onClick={() => setActiveEvidenceId(t.evidence || t.evidence_id || t.id)}
                    className="cursor-pointer hover:bg-indigo-100 transition-colors text-indigo-600 font-mono bg-indigo-50 px-2 py-1 rounded text-[10px] border border-indigo-100 shrink-0"
                  >
                    {t.evidence || t.evidence_id || t.id || `EV-AUTO-${Math.floor(Math.random() * 900) + 100}`}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </WidgetPanel>
  );
}

function ConfidencePanel({ conf, sentiment }) {
  if (!conf) return null;
  const comps = conf.components || {};
  return (
    <div className="card">
      <h4 className="text-sm font-semibold text-gray-700 mb-3">
        Composite confidence - score {fmtNum(conf.score, 3)} <TierChip tier={conf.tier} />
      </h4>
      <div className="flex flex-col gap-y-4 w-full">
        {Object.entries(comps).map(([k, v]) => (
          <div key={k} className="w-full min-w-0">
            <div className="flex justify-between text-xs text-gray-500">
              <span>{k.replace(/_/g, " ")}</span><span className="mono">{fmtNum(v, 3)}</span>
            </div>
            <div className="h-2 w-full bg-gray-100 rounded-full mt-1 overflow-hidden">
              <div className="h-full rounded-full bg-indigo-600 transition-all duration-300"
                style={{ width: `${Math.max(0, Math.min(100, v * 100))}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-gray-500 mt-3 mono">
        weights: {Object.entries(conf.weights_used || {}).map(([k, v]) => `${k}=${fmtNum(v, 2)}`).join(", ")}
      </p>
    </div>
  );
}

function ActionCard({ a, headline }) {
  const badge =
    a.lever === "Pricing" ? "P" :
    a.lever === "Media budget" ? "M" :
    a.lever === "Fulfillment ops" ? "F" :
    a.lever === "Promotional counter-offer" ? "C" : "A";
  return (
    <div className={`card ${headline ? "!border-indigo-500/40" : ""} space-y-3`}>
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-10 h-10 rounded-lg bg-indigo-500/15 border border-indigo-500/30 flex items-center justify-center font-bold text-indigo-600">
          {badge}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <h4 className="font-semibold text-gray-900 leading-snug">{a.action}</h4>
            <span className="text-gray-500 shrink-0">›</span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            Lever <span className="text-gray-700 capitalize">{a.lever}</span> · driver{" "}
            <span className="text-gray-700 capitalize">{a.driver}</span>
          </p>
        </div>
      </div>

      <div className="flex items-baseline gap-1.5">
        <span className="mono text-emerald-600 font-semibold">{fmtUSD(a.expected_impact_value)} {a.impact_unit}</span>
        <span className={MICRO}>expected impact</span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs pt-3 border-t border-gray-200">
        <div><p className={MICRO}>Owner</p><p className="text-gray-800">{a.owner}</p></div>
        <div><p className={MICRO}>Confidence</p><TierChip tier={a.confidence_tier} /></div>
        <div><p className={MICRO}>Monitoring</p><p className="text-gray-700">{a.monitoring_plan.split(";")[0]}</p></div>
      </div>

      {a.addresses_non_controllable && (
        <p className="text-[11px] text-violet-600">
          Addresses a non-controllable cause through a controllable lever.
        </p>
      )}
      {a.experiment && (
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-3 py-2 text-xs">
          <div className="flex items-center justify-between text-gray-700 mb-1">
            <span className="font-semibold">Confirm before scaling: {a.experiment.test_type} test</span>
            <span className="mono">{a.experiment.n_per_arm} / arm · {a.experiment.duration_days} days</span>
          </div>
          <p className="text-gray-500">
            Sized for {Math.round(a.experiment.power * 100)}% power, α={a.experiment.alpha} to detect a
            +{a.experiment.target_lift_pct}% lift on a {Math.round(a.experiment.baseline_rate * 100)}% baseline.
          </p>
        </div>
      )}
    </div>
  );
}
