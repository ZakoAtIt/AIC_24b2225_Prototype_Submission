import { useEffect, useState } from "react";
import { api } from "../api";

export default function CorrectionChips() {
  const [corrections, setCorrections] = useState({});

  useEffect(() => {
    api.feedbackHistory()
      .then((r) => setCorrections(r.corrections || {}))
      .catch(() => {});
  }, []);

  const entries = Object.entries(corrections);
  if (!entries.length) return null;

  return (
    <div className="card">
      <h4 className="text-sm font-semibold text-gray-700 mb-2">
        Correction memory - what humans keep telling us
      </h4>
      <div className="flex flex-wrap gap-2">
        {entries.map(([driver, n]) => (
          <span key={driver} className="chip-violet" title="Human 'correct' feedback count - damps action ranking">
            {driver} x {n}
          </span>
        ))}
      </div>
      <p className="text-[11px] text-gray-500 mt-2">
        Corrections never fabricate evidence; they only reorder candidate levers (max +25% tiebreak)
        and nudge live confidence weights.
      </p>
    </div>
  );
}
