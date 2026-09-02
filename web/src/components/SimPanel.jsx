import { useEffect, useState } from "react";
import { api, fmtPct, fmtUSD } from "../api";
import SeriesChart from "./SeriesChart";

export default function SimPanel({ role, userRegion, series, events, showEvents, setShowEvents }) {
  const [sim, setSim] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setErr("");
    try {
      setSim(await api.recoverySim({ role, userRegion }));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { run(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="space-y-4">
      <div className="card border-amber-200 !bg-amber-500/5">
        <p className="text-xs text-amber-600">
          SIMULATION - counterfactual arithmetic on measured effects, not an observation.
          Applies the recommended levers (logistics fix, promo counter-offer, pricing rollback
          test) to the transaction replay and re-runs the detector.
        </p>
      </div>

      {busy && <p className="text-sm text-gray-500">Running counterfactual...</p>}
      {err && <p className="text-sm text-rose-600">{err}</p>}

      {series?.period?.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
            <h4 className="text-sm font-semibold text-gray-700">Detection diagnostics</h4>
            <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer">
              <input type="checkbox" checked={!!showEvents}
                onChange={(e) => setShowEvents(e.target.checked)} />
              Overlay planted events (demo diagnostics)
            </label>
          </div>
          <SeriesChart series={series} events={events || []} showEvents={showEvents} />
        </div>
      )}

      {sim && (
        <>
          <div className={`card ${sim.recovers_materiality ? "!border-emerald-200" : ""}`}>
            <p className="text-sm text-gray-800">
              {sim.recovers_materiality
                ? "Every adversely-down region exits its material band after applying the levers."
                : sim.per_region.some((r) => r.was_adversely_down)
                  ? "Partial recovery - some regions remain outside their material band."
                  : "No region is currently adversely down in this scope."}
            </p>
          </div>

          <div className="card overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead>
                <tr>
                  <th className="th">Region</th>
                  <th className="th">Before (dev / z)</th>
                  <th className="th">After (dev / z)</th>
                  <th className="th">$ / day recovered</th>
                  <th className="th">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {sim.per_region.map((r) => (
                  <tr key={r.region}>
                    <td className="td font-medium">{r.region}</td>
                    <td className="td mono text-xs">
                      <span className={r.was_adversely_down ? "text-rose-600" : ""}>
                        {fmtPct(r.before.pct_deviation)} / z {r.before.z_score}
                      </span>
                    </td>
                    <td className="td mono text-xs">{fmtPct(r.after.pct_deviation)} / {r.after.z_score}</td>
                    <td className="td mono text-xs text-emerald-600">
                      {fmtUSD(r.avg_daily_net_revenue_in_window.delta_usd_per_day)}
                    </td>
                    <td className="td">
                      {r.was_adversely_down
                        ? (r.recovered ? <span className="chip-emerald">RECOVERED</span>
                                       : <span className="chip-red">STILL DOWN</span>)
                        : <span className="chip-slate">not affected</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-[11px] text-gray-500">{sim.note}</p>
        </>
      )}
    </div>
  );
}
