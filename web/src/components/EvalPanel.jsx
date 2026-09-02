import { useEffect, useState } from "react";
import { api } from "../api";

const FLAG_CLS = {
  major_shift: "chip-red",
  notable_shift: "chip-amber",
  stable: "chip-emerald",
};

export default function EvalPanel() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.evaluation().then(setData).catch((e) => setErr(e.message));
  }, []);

  if (err) return <p className="text-sm text-rose-600">{err}</p>;
  if (!data) return <p className="text-sm text-gray-500">Running multi-seed evaluation sweep...</p>;

  const sc = data.detector_scorecard;
  const conf = sc.confusion;

  return (
    <div className="space-y-4">
      <div className="card !border-violet-200">
        <p className="text-xs text-violet-600">{data.warning}</p>
      </div>

      {/* detector scorecard */}
      <div className="card">
        <h4 className="text-sm font-semibold text-gray-700 mb-3">
          Detector scorecard - {sc.seeds.length} independent noise seeds, same event design
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-3">
          {[["Precision", sc.precision], ["Recall", sc.recall],
            ["True positives", conf.tp], ["False positives", conf.fp],
            ["False negatives", conf.fn], ["True negatives", conf.tn]].map(([l, v]) => (
            <div key={l} className="bg-gray-50 rounded-lg p-3">
              <p className="text-[10px] uppercase tracking-wider text-gray-500">{l}</p>
              <p className={`text-lg font-bold mono ${l === "Precision" || l === "Recall" ? "text-indigo-600" : "text-gray-800"}`}>
                {v}
              </p>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-gray-500">{sc.note}</p>

        {sc.misses.length > 0 && (
          <div className="mt-3">
            <p className="text-xs font-semibold text-gray-500 mb-1">Residual errors (shown on purpose)</p>
            <table className="w-full text-xs">
              <thead><tr><th className="th">Seed</th><th className="th">Slice</th><th className="th">Truth</th>
                <th className="th">Predicted</th><th className="th">Dev</th><th className="th">z</th></tr></thead>
              <tbody>
                {sc.misses.map((m, i) => (
                  <tr key={i}>
                    <td className="td mono">{m.seed}</td>
                    <td className="td">{m.kpi}/{m.region}</td>
                    <td className="td">{m.truth}</td>
                    <td className="td">{m.pred}</td>
                    <td className="td mono">{m.dev_pct}%</td>
                    <td className="td mono">{m.z}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[11px] text-gray-500 mt-2">
              Noisy ratio KPIs (margin %, return rate) can pass both gates on unlucky draws -
              exactly why flags feed a confidence composite and DiD before anyone acts.
            </p>
          </div>
        )}
      </div>

      {/* drift */}
      <div className="card">
        <h4 className="text-sm font-semibold text-gray-700 mb-1">PSI drift monitor</h4>
        <p className="text-[11px] text-gray-500 mb-3">
          Population Stability Index, weekday-deseasonalized. &gt;0.25 notable, &gt;0.5 major.
          Drift means the expected-baseline model needs re-fitting - distinct from an anomaly.
        </p>
        <table className="w-full">
          <thead><tr><th className="th">Slice</th><th className="th">PSI</th><th className="th">Flag</th>
            <th className="th">Windows</th><th className="th">Why it matters</th></tr></thead>
          <tbody>
            {data.drift.map((d) => (
              <tr key={d.slice}>
                <td className="td mono text-xs">{d.slice}</td>
                <td className="td mono">{d.psi}</td>
                <td className="td"><span className={FLAG_CLS[d.flag]}>{d.flag.replace("_", " ")}</span></td>
                <td className="td text-xs text-gray-500">{d.baseline_window} → {d.current_window}</td>
                <td className="td text-xs max-w-[360px]">{d.why_it_matters}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
