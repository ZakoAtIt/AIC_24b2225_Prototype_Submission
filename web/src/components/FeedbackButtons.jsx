import { useState } from "react";
import { api } from "../api";

const DRIVERS = [
  ["Competitor Action", "competitor_activity"],
  ["Supply Chain / Logistics", "logistics_performance"],
  ["Macroeconomic Seasonality", "seasonality"],
  ["Pricing Elasticity", "price"],
  ["Product Quality", "returns_rate"],
];

export default function FeedbackButtons({ insightId, kpiId }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [driver, setDriver] = useState(DRIVERS[0][1]);

  const send = async (action) => {
    setBusy(true);
    try {
      const res = await api.feedback({
        insight_id: insightId,
        action,
        corrected_driver: action === "correct" ? driver : null,
        correction_text: null,
        kpi_id: kpiId,
      });
      setResult(res);
      setCorrecting(false);
    } catch (e) {
      setResult({ message: e.message });
    } finally {
      setBusy(false);
    }
  };

  const integration = result?.integration;

  return (
    <div className="card">
      <h4 className="text-sm font-semibold text-gray-700 mb-2">Was this analysis useful?</h4>
      <div className="flex gap-2 flex-wrap items-center">
        <button className="btn-indigo" disabled={busy} onClick={() => send("accept")}>Accept</button>
        <button className="btn-ghost" disabled={busy} onClick={() => send("reject")}>Reject</button>
        <button className="btn-ghost" disabled={busy} onClick={() => setCorrecting(!correcting)}>Correct</button>
      </div>
      {correcting && (
        <div className="mt-2 space-y-2">
          <label className="block text-xs text-gray-500">
            Which driver best explains the deviation?
          </label>
          <select
            value={driver}
            onChange={(e) => setDriver(e.target.value)}
            className="bg-gray-100 border border-gray-200 rounded-lg px-2 py-1.5 text-sm w-full"
          >
            {DRIVERS.map(([label, token]) => (
              <option key={token} value={token}>{label}</option>
            ))}
          </select>
          <button className="btn-indigo" disabled={busy} onClick={() => send("correct")}>
            Submit correction
          </button>
        </div>
      )}

      {integration && (
        <div className="mt-3 bg-gray-50 border border-violet-200 rounded-lg p-3 space-y-1.5">
          <p className="text-xs font-semibold text-violet-600">INTEGRATION RECEIPT</p>
          <p className="text-xs text-gray-800">
            Understood as: <span className="chip-indigo mono">{integration.understood_as}</span>{" "}
            <span className="text-gray-500">
              (match confidence {Number(integration.match_confidence).toFixed(2)}, via{" "}
              {integration.method_type})
            </span>
          </p>
          <ul className="text-xs text-gray-500 list-disc list-inside space-y-0.5">
            {integration.integrated_into.map((x) => <li key={x}>{x}</li>)}
          </ul>
          {result.weights_after && (
            <p className="text-xs text-gray-500">
              Confidence weights:{" "}
              <span className="mono text-gray-500 line-through mr-1">
                {Object.values(result.weights_before || {}).map((v) => Number(v).toFixed(2)).join("/")}
              </span>
              →
              <span className="mono text-emerald-600 ml-1">
                {Object.values(result.weights_after).map((v) => Number(v).toFixed(2)).join("/")}
              </span>
            </p>
          )}
        </div>
      )}

      {!integration && result?.message && (
        <div className="mt-3 text-xs text-gray-500">{result.message}</div>
      )}
    </div>
  );
}
