"""Data store: fresh-from-disk source DataFrames + an IN-MEMORY DuckDB.

Chaos-testing contract:
  * The operational DB is purely in-memory (`duckdb.connect(':memory:')`).
    It NEVER writes to a persistent `.duckdb` / `.db` file, so no file lock is
    ever held - not even while the server idles. Telemetry, feedback and live
    weights live for the life of the process and are re-seeded on restart.
  * Source CSVs are re-read from disk on EVERY call (no process-level
    singleton caching), so the pipeline always reacts to real-time CSV
    mutations instead of serving a stale snapshot.
  * Connections are opened lazily and closed explicitly via close_conn().
"""

import threading
from pathlib import Path

import duckdb
import pandas as pd

from app.config import DATA_DIR

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def get_pos() -> pd.DataFrame:
    """Fresh read of pos_transactions.csv on every call (no caching)."""
    path = Path(DATA_DIR) / "pos_transactions.csv"
    if not path.exists():
        raise FileNotFoundError("pos_transactions.csv missing - run generate_data.py")
    return pd.read_csv(path, parse_dates=["date"])


def get_marketing() -> pd.DataFrame:
    """Fresh read of marketing_spend.csv on every call (no caching)."""
    path = Path(DATA_DIR) / "marketing_spend.csv"
    if not path.exists():
        raise FileNotFoundError("marketing_spend.csv missing - run generate_data.py")
    return pd.read_csv(path, parse_dates=["week_start"])


def get_tickets() -> pd.DataFrame:
    """Fresh read of support_tickets.csv on every call (no caching)."""
    path = Path(DATA_DIR) / "support_tickets.csv"
    if not path.exists():
        raise FileNotFoundError("support_tickets.csv missing - run generate_data.py")
    return pd.read_csv(path)


def get_conn() -> duckdb.DuckDBPyConnection:
    """Return the process-wide IN-MEMORY DuckDB connection.

    Never connects to a file on disk, so no `.duckdb`/`.db` file lock can be
    held while the server idles. Operational tables are created/lazy-seeded on
    first use and persist for the life of the process.
    """
    global _conn
    with _lock:
        if _conn is None:
            _conn = duckdb.connect(":memory:")
            _conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    request_id VARCHAR, ts TIMESTAMP, route VARCHAR, params VARCHAR,
                    total_ms DOUBLE, stages_json VARCHAR, llm_calls INTEGER,
                    prompt_tokens INTEGER, completion_tokens INTEGER,
                    est_cost_usd DOUBLE, cache_hit BOOLEAN
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id INTEGER PRIMARY KEY, ts TIMESTAMP, insight_id VARCHAR,
                    action VARCHAR, corrected_driver VARCHAR, kpi_id VARCHAR
                );
                CREATE TABLE IF NOT EXISTS confidence_weights (
                    weight_key VARCHAR PRIMARY KEY, value DOUBLE, updated_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS narrative_cache (
                    cache_key VARCHAR PRIMARY KEY, response_json VARCHAR,
                    model VARCHAR, created_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evidence_log (
                    request_id VARCHAR, ts TIMESTAMP, kpi_id VARCHAR,
                    evidence_id VARCHAR, claim VARCHAR, value DOUBLE,
                    unit VARCHAR, source VARCHAR, method VARCHAR, method_type VARCHAR
                );
                """
            )
    return _conn


def close_conn() -> None:
    """Close the in-memory connection cleanly.

    Safe to call after a request or on shutdown; the next get_conn() reopens a
    fresh in-memory DB and re-seeds the schema. Because the DB is in-memory,
    closing never needs to flush/release a file lock.
    """
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
