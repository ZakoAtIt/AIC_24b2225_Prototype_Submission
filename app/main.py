"""FastAPI application entry point.

Run locally from the project root with:
    .venv\\Scripts\\python -m uvicorn app.main:app --reload --port 8000
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import DATA_DIR, FRONTEND_ORIGINS
from app.services import telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """On startup, delete any stale persistent DuckDB files in data/.

    The operational DB is purely in-memory (store.get_conn uses
    ':memory:'), so a lingering causetrace.duckdb / *.db is always a leftover
    that could be locked by a killed-but-not-reaped server process. Removing
    every such file at startup guarantees the pipeline can never collide on a
    file lock and always starts from a fresh, consistent state.
    """
    data_dir = Path(DATA_DIR)
    if data_dir.exists():
        for p in list(data_dir.glob("*.duckdb")) + list(data_dir.glob("*.db")):
            try:
                p.unlink(missing_ok=True)
                print(f"[causetrace] removed stale db file: {p.name}")
            except OSError as e:
                print(f"[causetrace] could not remove {p.name}: {e}")
    yield


app = FastAPI(
    title="CauseTrace API",
    description="KPI Intelligence-to-Action Engine - deterministic pipeline, narrating LLM.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """Times the whole request and persists stage/LLM telemetry to DuckDB.
    The request id rides back on a response header so the frontend can link
    an insight card to its processing trace."""
    st = telemetry.init_request()
    t0 = time.perf_counter()
    response = await call_next(request)
    total_ms = (time.perf_counter() - t0) * 1000
    rid = telemetry.persist(request.url.path, str(request.url.query), total_ms)
    response.headers["X-Request-Id"] = rid
    return response


app.include_router(router)


@app.get("/health")
def health():
    """Liveness probe used by the frontend and the smoke tests."""
    return {"status": "ok", "service": "causetrace-api"}
