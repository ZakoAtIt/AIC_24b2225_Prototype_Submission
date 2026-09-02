# CauseTrace

**A KPI intelligence engine that explains *why* your numbers moved — without ever letting an LLM make up a number.**

Most "AI BI" tools ask an LLM to look at a chart and narrate it. The problem is that LLMs are fluent, not accurate — they'll happily invent a percentage that sounds right but isn't. CauseTrace takes a different approach: every number in the system is computed by deterministic code first. The LLM is only ever handed numbers that already exist and asked to explain them in plain English. If it tries to introduce a number that isn't in our evidence store, that output gets thrown away and replaced with a safe, pre-written template instead.

That one rule — **the LLM explains, it never calculates** — shapes everything else in this project.

---

## What it actually does

Point CauseTrace at your business data (sales, marketing spend, support tickets — anything with a timestamp) and it will:

1. **Spot the movements that actually matter.** Not every wiggle in a KPI is worth flagging — weekends, month-end, and seasonal patterns all cause "noise" that looks like a spike if you're not careful. We decompose each series into trend/seasonality/residual (STL) and compare against the same day-of-week historically, so a Saturday is only ever compared to other Saturdays.

2. **Break down exactly what caused a change.** If revenue dropped 8%, how much of that is fewer customers vs. lower prices vs. a shift in what people bought vs. more returns? We compute an exact waterfall (price × volume × mix × returns) so the components always add up to the total — no hand-waving.

3. **Test whether a suspected cause is real.** A competitor promo in one region and a support outage in another can hit your numbers in the same week. We use difference-in-differences — comparing the affected region against an unaffected "control" region over the same period — to isolate each effect instead of guessing which one mattered more.

4. **Know when it doesn't know enough.** If the data needed to explain a movement is missing, stale, or contradicts itself (e.g. margins are up but customer sentiment is cratering), the system lowers its own confidence score and will refuse to generate a narrative rather than bluff. It asks for the missing piece instead.

5. **Turn the diagnosis into next steps.** Each identified driver maps to a controllable lever (e.g. "price" → "run a targeted discount"), with an estimated dollar impact, a suggested owner, and what to keep an eye on afterward.

6. **Get better with use.** When an analyst corrects or confirms a finding, that feedback is stored and used to adjust confidence weighting going forward — the system's calibration shifts with real-world corrections instead of staying static.

7. **Speak differently to different audiences.** A CFO gets a short, plain-language brief. An analyst gets the full breakdown with every intermediate number shown and traceable.

---

## How data flows through the system

```
raw CSVs
   │
   ▼
1. load & validate ─────────── checks schema, types, freshness per source
   │
2. semantic resolver ────────── maps raw columns to known KPI definitions,
   │                             applies role-based access (an analyst only
   │                             sees their region; costs are masked)
   │
3. anomaly detection ────────── STL decomposition + weekday-matched z-scores
   │                             (falls back to a simpler rolling z-score for
   │                             brand-new products with too little history)
   │
4. decomposition ────────────── exact price / volume / mix / returns waterfall
   │
5. causal testing ────────────── difference-in-differences across control groups
   │                             to isolate overlapping effects
   │
6. confidence scoring ────────── weighs how much evidence supports each finding;
   │                             contradicting or missing evidence lowers the
   │                             score and can trigger an abstain
   │
7. action mapping ────────────── driver → lever → recommended action → owner
   │
8. narration (LLM) ──────────── the ONLY step that touches a language model.
   │                             Output is strict JSON, and every number in it
   │                             is checked against what was actually computed
   │                             in steps 3–7. Anything that doesn't match is
   │                             discarded and replaced with a template.
   │
9. persona routing ───────────── formats the (validated) result as a CFO brief
                                  or a full analyst breakdown
```

Everything before step 8 is plain Python — pandas, numpy, scipy, statsmodels. Nothing in there can hallucinate because none of it is a language model. Step 8 is deliberately the only place an LLM touches the pipeline, and its output is checked, not trusted.

We also built our own synthetic data generator (`generate_data.py`) that simulates a multi-region retail business with specific, known events baked in (a logistics delay, a competitor promo, a price change) — that way we can check the pipeline actually finds the causes we planted, not just plausible-looking ones.

---

## Running it locally

You'll need:
- **Python 3.10+**
- **Node.js 18+**

These steps work the same on Windows, macOS, and Linux — the only difference is how you activate a Python virtual environment (noted below).

### 1. Set up your environment file

From the project root:

```bash
cp .env.example .env
```

Open `.env` and check these values:

```ini
# A fixed "current time" so freshness/SLA checks are consistent against the
# sample data. Only relevant if you're using the bundled sample dataset.
DEMO_NOW=2026-07-30

# 'mock' runs entirely offline with deterministic, instant responses —
# no API key needed, good for just trying things out.
# Set this to 'gemini' and add GEMINI_API_KEY below to use a real LLM
# for the narration step.
LLM_PROVIDER=mock
```

### 2. Start the backend (FastAPI + DuckDB)

```bash
# from the project root
python -m venv .venv

# activate it:
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows (Command Prompt or PowerShell)

pip install -r requirements.txt

# generate the sample dataset (skip this if you're plugging in your own data)
python generate_data.py --scenario default

# start the API
python -m uvicorn app.main:app --port 8000
```

The API is now running at `http://localhost:8000`.

### 3. Start the frontend (React + Vite)

In a **second terminal**, from the project root:

```bash
cd web
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. It talks to the backend automatically.

### Using your own data instead of the sample set

Drop CSVs matching the schema described in `kpi_contracts.json` into the expected data folder and skip the `generate_data.py` step. The semantic resolver will validate the schema on load and tell you specifically what's missing or mistyped if something doesn't match.

---

## Trying out the failure-handling behavior

The interesting part of this project isn't the happy path — it's what happens when data goes wrong. There's a script that simulates real pipeline problems (a data source going missing, a schema change, an outlier flooding in) so you can see the system react without touching any code.

With both the backend and frontend running, open a third terminal in the project root:

**Simulate a missing data source:**

```bash
python judge_test.py --test missing_data
```

Hard-refresh the browser (Ctrl/Cmd + Shift + R). Nothing crashes — instead, the affected KPI card and its narrative switch to a `VALIDATION FAILED` or `ANALYSIS WITHHELD` state. What's happening under the hood: marketing spend data disappeared, so the confidence engine can no longer support a full explanation, drops below its threshold, and the system withholds the narrative rather than guessing.

**Put things back:**

```bash
python judge_test.py --test restore
```

Hard-refresh again and everything returns to its normal, fully-supported state.

(Open `judge_test.py` if you want to see the other scenarios it can simulate, like schema drift or injected outliers.)

---

## Running the test suite

```bash
# checks each stage of the pipeline in isolation
python -m pytest tests/test_phase2_pipeline.py -q

# full end-to-end integration checks across the whole stack
python tests/test_phase4_integration.py
```

---

## A few design decisions worth knowing about

- **Row/column-level security is enforced in the data layer, not the UI.** Switching to an analyst view doesn't just hide a column visually — the backend physically excludes restricted fields (like unit cost) and rows outside that analyst's region from the response payload.
- **New products don't break the anomaly detector.** STL decomposition needs a few full seasonal cycles of history to work properly. If a product is too new for that, detection quietly falls back to a simpler rolling z-score instead of erroring out or refusing to analyze it.
- **Support ticket text is only pulled in when the math justifies it.** We compute a sentiment drift score from ticket volume/language first; only if that shows a statistically meaningful drop do we go retrieve the actual ticket text to use as supporting context — so the LLM isn't fed unstructured text unless there's already numeric evidence something is wrong.
- **Every LLM call is logged and costed**, down to token counts and fractional cents, so it's possible to see exactly what the narration step is costing at any point.
