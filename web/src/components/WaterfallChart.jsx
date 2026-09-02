import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell, CartesianGrid, ReferenceLine,
} from "recharts";
import { fmtUSD } from "../api";

const COLORS = {
  price: "#f59e0b",
  volume: "#38bdf8",
  mix: "#a78bfa",
  returns: "#fb7185",
  returns_rate: "#fb7185",
  competitor_activity: "#ec4899",
  logistics_performance: "#8b5cf6",
};

const CustomXAxisTick = ({ x, y, payload }) => {
  const text = payload.value;
  const truncated = text.length > 25 ? text.substring(0, 25) + '...' : text;
  return (
    <g transform={`translate(${x},${y})`}>
      <text x={0} y={0} dy={16} textAnchor="middle" fill="#64748b" fontSize={10}>
        <title>{text}</title>
        {truncated}
      </text>
    </g>
  );
};

export default function WaterfallChart({ drivers, total }) {
  if (!drivers?.length) return null;

  // Stepped bridge using Recharts' native range-dataKey: each bar carries
  // range:[start, cumulative] so it floats between its previous and current
  // cumulative value. No transparent placeholder / stackId needed (a single
  // stack would tear positive and negative segments into different bands).
  let cumulative = 0;
  const data = [{ name: "Baseline", range: [0, 0], rawEffect: 0, role: "baseline" }];

  drivers.forEach((d) => {
    const val = Number(d.effect) || 0;
    const start = cumulative;
    cumulative += val;

    data.push({
      name: d.driver.replace(/_/g, " "),
      range: [start, cumulative],
      rawEffect: val,
      evidence_id: d.evidence_id,
      role: "driver",
    });
  });

  const finalTotal = total != null ? Number(total) : cumulative;
  data.push({
    name: "Actual",
    range: [0, finalTotal],
    rawEffect: finalTotal,
    role: "actual",
  });

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 40 }}>
          <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="name" tick={<CustomXAxisTick />} />
          <YAxis tick={{ fill: "#64748b", fontSize: 10 }} width={70}
            tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
          <Tooltip
            formatter={(v, name, item) => {
              const p = item?.payload;
              if (p?.role === "driver") {
                return [`${fmtUSD(p.rawEffect)} contribution`, "Driver Impact"];
              }
              if (p?.role === "actual") return [fmtUSD(finalTotal), "Actual Net Change"];
              return ["$0.00", "Baseline Level"];
            }}
            labelFormatter={(label, payload) => {
              const p = payload?.[0]?.payload;
              return p?.role === "driver" && p?.evidence_id ? `${label} - ${p.evidence_id}` : label;
            }}
            contentStyle={{ background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: 8, boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)" }}
          />
          <ReferenceLine y={0} stroke="#94a3b8" />
          {/* Range bars float between start and cumulative on the native dataKey */}
          <Bar dataKey="range" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {data.map((entry, index) => {
              let fill = "#94a3b8"; // baseline
              if (entry.role === "actual") fill = "#6366f1";
              else if (entry.role === "driver") {
                fill = COLORS[entry.name.replace(/ /g, "_")]
                  || (entry.rawEffect >= 0 ? "#10b981" : "#f43f5e");
              }
              return <Cell key={`cell-${index}`} fill={fill} />;
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-6 bg-slate-50 border border-slate-100 rounded-md p-3">
        <p className="text-[10px] text-slate-500 leading-relaxed">
          <strong className="font-semibold text-indigo-600">How to read:</strong> The grey Baseline sits at zero; each bar bridges the running total as one driver contributes. The indigo Actual bar is the net KPI change, derived from an exact price/volume/mix/returns decomposition.
        </p>
      </div>
    </div>
  );
}
