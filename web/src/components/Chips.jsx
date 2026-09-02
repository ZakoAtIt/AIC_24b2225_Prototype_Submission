const METHOD_CHIP = {
  LLM: "chip-violet",
  statistics: "chip-indigo",
  deterministic_arithmetic: "chip-emerald",
  causal_inference: "chip-amber",
  business_rules: "chip-slate",
  scoring_rules: "chip-slate",
  retrieval: "chip-slate",
  data_engineering: "chip-slate",
};

export function MethodChip({ type }) {
  return <span className={METHOD_CHIP[type] || "chip-slate"}>{type || "n/a"}</span>;
}

export function StatusChip({ status }) {
  const map = {
    adverse: ["chip-red", "ADVERSE"],
    favorable: ["chip-emerald", "FAVORABLE"],
    watch: ["chip-amber", "WATCH"],
    normal: ["chip-emerald", "NORMAL"],
    Contradictory: ["chip-red", "CONTRADICTORY"],
  };
  const [cls, label] = map[status] || ["chip-slate", String(status).toUpperCase()];
  return <span className={cls}>{label}</span>;
}

export function TierChip({ tier }) {
  const map = {
    "Observed": "chip-emerald",
    "Strongly Supported": "chip-emerald",
    "Likely": "chip-indigo",
    "Possible": "chip-amber",
    "Insufficient Evidence": "chip-red",
    "Contradictory": "chip-red",
  };
  return <span className={map[tier] || "chip-slate"}>{tier}</span>;
}

export default { MethodChip, StatusChip, TierChip };
