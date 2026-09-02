import { useEffect, useState } from "react";
import { api, fmtNum } from "../api";
import { MethodChip } from "./Chips";

export default function EvidencePanel({ kpiId }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.evidence(kpiId).then(setData).catch((e) => setErr(e.message));
  }, [kpiId]);

  if (err) return <p className="text-sm text-rose-600">{err}</p>;
  if (!data) return <p className="text-sm text-gray-500">Loading...</p>;

  return (
    <div className="space-y-4">
      <div className="card">
        <h4 className="text-sm font-semibold text-gray-700 mb-3">Source freshness</h4>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {data.freshness.map((f) => (
            <div key={f.source} className="bg-gray-50 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-gray-800">{f.source}</span>
                {f.stale ? <span className="chip-amber">STALE</span> : <span className="chip-emerald">FRESH</span>}
              </div>
              <p className="text-xs text-gray-500 mt-1 mono">
                age {f.age_hours}h / SLA {f.sla_hours}h - {f.grain}
              </p>
            </div>
          ))}
        </div>
      </div>

      <div className="card overflow-x-auto">
        <h4 className="text-sm font-semibold text-gray-700 mb-2">Evidence registry</h4>
        <table className="w-full min-w-[820px]">
          <thead>
            <tr>
              <th className="th">ID</th><th className="th">Claim</th>
              <th className="th text-right">Value</th><th className="th">Source</th>
              <th className="th">Method</th><th className="th">Type</th>
            </tr>
          </thead>
          <tbody>
            {data.evidence.map((e) => (
              <tr key={e.evidence_id} className="hover:bg-slate-50 even:bg-slate-50/50">
                <td className="td mono text-xs text-indigo-600">{e.evidence_id}</td>
                <td className="td text-xs max-w-[320px]">{e.claim}</td>
                <td className="td text-right tabular-nums">
                  {typeof e.value === "number" ? fmtNum(e.value) : String(e.value)}{" "}
                  <span className="text-gray-500">{e.unit || ""}</span>
                </td>
                <td className="td text-xs">{e.source}</td>
                <td className="td text-xs">{e.method}</td>
                <td className="td"><MethodChip type={e.method_type} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h4 className="text-sm font-semibold text-gray-700 mb-2">KPI lineage</h4>
        <ol className="list-decimal list-inside space-y-1 text-xs text-gray-500">
          {(data.lineage || []).map((l) => <li key={l} className="mono">{l}</li>)}
        </ol>
        <p className="text-[11px] text-gray-500 mt-2">{data.note}</p>
      </div>
    </div>
  );
}
