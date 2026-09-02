import { useEffect, useState } from "react";
import { api } from "../api";
import { MethodChip } from "./Chips";

function TraceRow({ request }) {
  const [trace, setTrace] = useState(null);
  const [open, setOpen] = useState(false);

  const toggle = () => {
    if (!open && !trace) api.audit(request.request_id).then(setTrace).catch(() => {});
    setOpen(!open);
  };

  return (
    <>
      <tr className="cursor-pointer hover:bg-gray-50" onClick={toggle}>
        <td className="td mono text-xs">{request.request_id} {open ? "▾" : "▸"}</td>
        <td className="td text-xs">{request.route}</td>
        <td className="td mono text-xs">{request.total_ms} ms</td>
        <td className="td">
          <div className="flex flex-wrap gap-1">
            {(request.stages || []).map((st, i) => (
              <span key={i} title={`${st.stage}: ${st.ms}ms`}>
                <MethodChip type={st.method_type} />
              </span>
            ))}
            {!request.stages?.length && <span className="chip-slate">no pipeline</span>}
          </div>
        </td>
        <td className="td mono text-xs">{request.llm_calls || "-"}</td>
        <td className="td">{request.cache_hit ? <span className="chip-emerald">HIT</span> : <span className="chip-slate">miss</span>}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} className="td bg-gray-50">
            {!trace ? "Loading trace..." : (
              <div className="py-2 space-y-2">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Stage timings</p>
                  <div className="flex flex-wrap gap-2 text-xs mono">
                    {trace.request.stages.map((s, i) => (
                      <span key={i} className="bg-gray-100 rounded px-2 py-0.5">
                        {s.stage}: {s.ms}ms
                      </span>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">
                    Evidence registered ({trace.evidence.length}) - immutable audit trail
                  </p>
                  <ul className="space-y-0.5 max-h-56 overflow-y-auto pr-2">
                    {trace.evidence.map((e) => (
                      <li key={e.evidence_id} className="text-xs flex gap-2 items-baseline">
                        <span className="mono text-indigo-600 shrink-0">{e.evidence_id}</span>
                        <span className="text-gray-700 flex-1">{e.claim}</span>
                        <span className="mono text-gray-500 shrink-0">
                          {e.value != null ? Number(e.value).toFixed(2) : "-"} {e.unit || ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function TelemetryPanel() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.telemetry(12).then(setData).catch((e) => setErr(e.message));
  }, []);

  if (err) return <p className="text-sm text-rose-600">{err}</p>;
  if (!data) return <p className="text-sm text-gray-500">Loading...</p>;

  const s = data.summary;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
        {[["Requests", s.requests],
          ["LLM calls", s.llm_calls],
          ["Tokens/insight", s.tokens_per_insight],
          ["Est. cost", `$${Number(s.est_cost_usd).toFixed(4)}`],
          ["Avg latency", `${s.avg_latency_ms} ms`],
          ["p50", s.p50_latency_ms != null ? `${s.p50_latency_ms} ms` : "-"],
          ["p95", s.p95_latency_ms != null ? `${s.p95_latency_ms} ms` : "-"],
          ["Cache hit rate", s.cache_hit_rate != null ? `${(s.cache_hit_rate * 100).toFixed(0)}%` : "-"],
        ].map(([label, val]) => (
          <div key={label} className="card !p-3">
            <p className="text-xs uppercase tracking-wider text-gray-500">{label}</p>
            <p className="text-lg font-semibold text-gray-900 mono">{val}</p>
          </div>
        ))}
      </div>

      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead>
            <tr>
              <th className="th">Request</th><th className="th">Route</th>
              <th className="th">Total</th><th className="th">Stage trace</th>
              <th className="th">LLM</th><th className="th">Cache</th>
            </tr>
          </thead>
          <tbody>
            {data.requests.map((r) => <TraceRow key={r.request_id} request={r} />)}
          </tbody>
        </table>
        <p className="text-[11px] text-gray-500 mt-2">Click a row to open its full audit trace (stage timings + every evidence item).</p>
      </div>
    </div>
  );
}
