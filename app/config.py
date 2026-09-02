"""Central configuration for CauseTrace.

Every environment-specific value lives here so that no other module reads
os.environ directly. Two ideas matter:

1. LLM_PROVIDER abstraction - the narrative engine runs against a mock
   (deterministic, free, offline), GitHub Models (real gpt-4o-mini, free),
   or the OpenAI platform (real gpt-4o-mini, pay-as-you-go). One env var
   switches providers; no pipeline code changes.

2. The frozen demo clock - synthetic data never refreshes, so freshness
   checks would eventually flag everything as stale. DEMO_NOW pins "today";
   if unset, the pipeline falls back to the newest data date.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")

# --- LLM ----------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()
assert LLM_PROVIDER in {"mock", "github", "openai", "gemini"}, (
    f"Unknown LLM_PROVIDER '{LLM_PROVIDER}' (expected mock|github|openai|gemini)"
)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
LLM_TIMEOUT_SECONDS = 30

# GitHub Models endpoint (free gpt-4o-mini access with a GitHub token).
# NOTE: the service is in scheduled-retirement brownout as of Aug 2026; kept
# for completeness - prefer 'gemini'.
GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"
GITHUB_MODELS_MODEL = "openai/gpt-4o-mini"

# Google AI Studio free tier via the OpenAI-compatible endpoint.
# New-format AQ.* keys authenticate with standard Bearer auth. gemini-2.x is
# retired for new users - 3.6-flash is the current fast tier. Free-tier quota
# is PER MODEL (~20 req/day), so we degrade through cheaper siblings before
# giving up entirely.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODELS = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

# Published list prices per 1M tokens, used ONLY for the estimated-cost
# readout in telemetry - never for logic. Gemini AI Studio free tier bills $0.
PRICE_INPUT_PER_M = 0.15
PRICE_OUTPUT_PER_M = 0.60
PROVIDER_PRICES = {
    "gemini": (0.0, 0.0),
    "mock": (0.0, 0.0),
    # github/openai fall back to the gpt-4o-mini list prices above.
}

# Hard cap on completion tokens for real provider calls. Thinking models
# (gemini-3.x-flash et al.) spend hidden reasoning tokens from THIS budget
# before emitting visible text - 700 starved them mid-sentence, so the floor
# accounts for both. Bounds worst-case cost per insight regardless.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "3072"))

# --- Demo clock ---------------------------------------------------------------
DEMO_NOW_ENV = os.getenv("DEMO_NOW", "").strip()

# --- Database -------------------------------------------------------------------
# Override (e.g. DUCKDB_PATH=%TEMP%\ct_test.duckdb) so test runs never contend
# with a live server for the single-writer DuckDB file.
DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", str(DATA_DIR / "causetrace.duckdb")))

# --- Confidence model defaults --------------------------------------------------
# Factory-reset weights for the composite confidence score. feedback_engine
# nudges live weights at runtime (damped, clamped); these are only defaults.
DEFAULT_CONFIDENCE_WEIGHTS = {
    "statistical_significance": 0.35,
    "did_clarity": 0.25,
    "cross_source_agreement": 0.20,
    "freshness": 0.20,
}
WEIGHT_STEP = 0.02            # max movement per single feedback event
WEIGHT_BOUNDS = (0.10, 0.50)  # clamp so no signal can be silenced entirely

# --- CORS ----------------------------------------------------------------------
FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
