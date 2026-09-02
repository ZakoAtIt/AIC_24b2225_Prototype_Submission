"""Per-request telemetry: stage timings, LLM usage, estimated cost.

Middleware opens a per-request context; pipeline stages record themselves;
middleware closes the request and persists one DuckDB row. The frontend
Telemetry panel reads GET /telemetry.

Stage records carry a `method_type` tag so the UI can show the explicit
LLM-vs-non-LLM processing breakdown required by the brief:
    data_engineering | business_rules | statistics | deterministic_arithmetic |
    causal_inference | scoring_rules | retrieval | LLM
"""

import json
import uuid
from contextvars import ContextVar

from app.services.store import get_conn

_state: ContextVar = ContextVar("causetrace_telemetry", default=None)


class _State:
    def __init__(self):
        self.request_id = str(uuid.uuid4())[:8]
        self.stages: list[dict] = []
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.est_cost_usd = 0.0
        self.cache_hit = False


def init_request() -> _State:
    st = _State()
    _state.set(st)
    return st


def current() -> _State | None:
    return _state.get()


def record_stage(name: str, method_type: str, ms: float):
    st = current()
    if st is not None:
        st.stages.append({
            "stage": name,
            "method_type": method_type,
            "ms": round(ms, 2),
        })


def record_llm(prompt_tokens: int, completion_tokens: int, cost_usd: float,
               cache_hit: bool):
    st = current()
    if st is not None:
        st.llm_calls += 0 if cache_hit else 1
        st.prompt_tokens += prompt_tokens
        st.completion_tokens += completion_tokens
        st.est_cost_usd += cost_usd
        st.cache_hit = st.cache_hit or cache_hit


def persist(route: str, params: str, total_ms: float):
    st = current()
    if st is None:
        return ""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO telemetry VALUES (?, now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            st.request_id, route, params, total_ms,
            json.dumps(st.stages), st.llm_calls,
            st.prompt_tokens, st.completion_tokens,
            round(st.est_cost_usd, 6), st.cache_hit,
        ],
    )
    return st.request_id


def log_evidence(request_id: str, kpi_id: str, items: list[dict]):
    """Persist the request's full evidence registry - the audit trail.
    Joinable to telemetry by request_id; powers GET /audit/{request_id}."""
    conn = get_conn()
    conn.execute("DELETE FROM evidence_log WHERE request_id = ?", [request_id])
    for it in items:
        v = it.get("value")
        conn.execute(
            "INSERT INTO evidence_log VALUES (?, now(), ?, ?, ?, ?, ?, ?, ?, ?)",
            [request_id, kpi_id, it.get("evidence_id"), it.get("claim"),
             float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None,
             it.get("unit"), it.get("source"), it.get("method"), it.get("method_type")],
        )


def audit_trace(request_id: str) -> dict | None:
    """Telemetry row + evidence rows for one request."""
    conn = get_conn()
    t = conn.execute(
        "SELECT request_id, ts, route, params, total_ms, stages_json, llm_calls,"
        " prompt_tokens, completion_tokens, est_cost_usd, cache_hit"
        " FROM telemetry WHERE request_id = ?", [request_id]).fetchone()
    if not t:
        return None
    ev = conn.execute(
        "SELECT evidence_id, kpi_id, claim, value, unit, source, method, method_type"
        " FROM evidence_log WHERE request_id = ? ORDER BY evidence_id", [request_id]).fetchall()
    return {
        "request": {
            "request_id": t[0], "ts": str(t[1]), "route": t[2], "params": t[3],
            "total_ms": round(t[4], 1), "stages": json.loads(t[5]),
            "llm_calls": t[6], "prompt_tokens": t[7], "completion_tokens": t[8],
            "est_cost_usd": t[9], "cache_hit": t[10],
        },
        "evidence": [
            {"evidence_id": r[0], "kpi_id": r[1], "claim": r[2], "value": r[3],
             "unit": r[4], "source": r[5], "method": r[6], "method_type": r[7]}
            for r in ev
        ],
    }


def recent(limit: int = 25) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT request_id, ts, route, params, total_ms, stages_json,
               llm_calls, prompt_tokens, completion_tokens, est_cost_usd, cache_hit
        FROM telemetry ORDER BY ts DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "request_id": r[0], "ts": str(r[1]), "route": r[2], "params": r[3] if len(r) > 3 else "{}",
            "total_ms": round(r[4], 1),
            "stages": json.loads(r[5]),
            "llm_calls": r[6], "prompt_tokens": r[7], "completion_tokens": r[8],
            "est_cost_usd": r[9], "cache_hit": r[10],
        })
    return out


def session_totals() -> dict:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(llm_calls),0), COALESCE(SUM(prompt_tokens),0),
               COALESCE(SUM(completion_tokens),0), COALESCE(SUM(est_cost_usd),0),
               COALESCE(AVG(total_ms),0),
               quantile_cont(total_ms, 0.5),
               quantile_cont(total_ms, 0.95),
               AVG(CASE WHEN cache_hit THEN 1.0 ELSE 0.0 END)
        FROM telemetry
        """
    ).fetchone()
    n = row[0] or 1
    return {
        "requests": row[0], "llm_calls": row[1],
        "prompt_tokens": row[2], "completion_tokens": row[3],
        "est_cost_usd": round(row[4], 6),
        "avg_latency_ms": round(row[5], 1),
        "p50_latency_ms": round(float(row[6]), 1) if row[6] is not None else None,
        "p95_latency_ms": round(float(row[7]), 1) if row[7] is not None else None,
        "cache_hit_rate": round(float(row[8]), 3) if row[8] is not None else None,
        "tokens_per_insight": round((row[2] + row[3]) / n, 1),
    }
