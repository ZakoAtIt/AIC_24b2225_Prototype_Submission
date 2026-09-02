const BASE = "http://localhost:8000";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw Object.assign(new Error(body.detail || res.statusText), { status: res.status });
  }
  return res.json();
}

export const api = {
  health: () => req("/health"),
  kpis: (region, persona) => req(`/kpis?` + new URLSearchParams({
    user_region: region || "South",
    persona: persona || "cfo",
  })),
  analysis: ({ kpiId, persona, role, userRegion, focusRegion }) =>
    req(`/kpis/${kpiId}/analysis?` + new URLSearchParams({
      persona, role,
      ...(userRegion ? { user_region: userRegion } : {}),
      ...(focusRegion && focusRegion !== "-" ? { focus_region: focusRegion } : {}),
    })),
  evidence: (kpiId) => req(`/kpis/${kpiId}/evidence`),
  feedback: (body) => req("/feedback", { method: "POST", body: JSON.stringify(body) }),
  audit: (requestId) => req(`/audit/${requestId}`),
  evaluation: () => req("/evaluation"),
  feedbackHistory: () => req("/feedback/history"),
  telemetry: (limit = 12) => req(`/telemetry?limit=${limit}`),
  recoverySim: ({ role, userRegion, focusRegion }) =>
    req(`/kpis/net_revenue/recovery-sim?` + new URLSearchParams({
      role,
      ...(userRegion ? { user_region: userRegion } : {}),
      ...(focusRegion && focusRegion !== "-" ? { focus_region: focusRegion } : {}),
    })),
  events: () => req("/events"),
};

export const fmtUSD = (v) =>
  v == null ? "-" : `${v < 0 ? "-" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
export const fmtPct = (v) => (v == null ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}%`);
export const fmtNum = (v, d = 2) => (v == null ? "-" : Number(v).toFixed(d));
