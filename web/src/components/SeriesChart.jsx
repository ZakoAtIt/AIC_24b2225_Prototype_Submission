import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceArea,
  CartesianGrid,
} from "recharts";

export default function SeriesChart({ series, events = [], showEvents }) {
  if (!series?.period?.length) return null;
  const data = series.period.map((p, i) => ({
    period: p.slice(5),
    actual: series.actual[i],
    baseline: series.baseline[i],
    low: series.band_low[i],
    high: series.band_high[i],
  }));
  const start = series.eval_window_start?.slice(5);

  return (
    <div className="h-56 w-full mt-4">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="period" tick={{ fill: "#64748b", fontSize: 10 }} minTickGap={28} />
          <YAxis tick={{ fill: "#64748b", fontSize: 10 }} width={54} />
          <Tooltip
            contentStyle={{ background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: 8, boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)" }}
            labelStyle={{ color: "#475569" }}
          />
          {showEvents && events.map((ev) => (
            <ReferenceArea key={ev.id} x1={ev.start.slice(5)} x2={ev.end.slice(5)}
              fill="#f43f5e" fillOpacity={0.07} stroke="#f43f5e" strokeOpacity={0.25}
              ifOverflow="extendDomain" />
          ))}
          <ReferenceArea x1={start} x2={data[data.length - 1].period}
            fill="#6366f1" fillOpacity={0.08} ifOverflow="extendDomain" />
          <Line type="monotone" dataKey="actual" stroke="#6366f1" strokeWidth={3} dot={false} name="Actual" />
          <Line type="monotone" dataKey="baseline" stroke="#94a3b8" strokeWidth={1.4} dot={false} strokeDasharray="5 4" name="Expected (STL trend+seasonal)" />
          <Line type="monotone" dataKey="low" stroke="#cbd5e1" strokeWidth={1} dot={false} opacity={0.7} name="95% band low" />
          <Line type="monotone" dataKey="high" stroke="#cbd5e1" strokeWidth={1} dot={false} opacity={0.7} name="95% band high" />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-slate-400 bg-slate-50 px-3 py-2 rounded mt-2 leading-relaxed">
        <span className="text-indigo-600 font-medium">How to read:</span> the indigo line is the
        actual KPI; the dashed grey line is its <em>expected level</em> from STL decomposition
        (trend + weekly seasonality), with the grey band showing the 95% confidence interval.
        The shaded strip on the right marks the evaluation window the statistical gates test.
        When "planted events" is on, red strips show ground-truth event windows - purely for
        judging detection quality; the pipeline never sees them.
      </p>
    </div>
  );
}
