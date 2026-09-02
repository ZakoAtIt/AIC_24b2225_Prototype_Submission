# CauseTrace
**KPI Intelligence-to-Action Engine**

*Accenture Innovation Challenge 2026 - BusinessIntelligence.ai Track*

CauseTrace is an enterprise-grade, deterministic intelligence engine that monitors business KPIs, detects *material* movements, mathematically decomposes their root causes, and generates actionable narratives.

We built CauseTrace on one uncompromising architectural rule to solve the hallucination crisis in BI:
> **The LLM explains numbers it is given; it never produces them.** Every quantitative claim must trace back to our deterministic Evidence Registry, or the system safely abstains.

---

## 🏆 Judging Rubric Alignment

We engineered this prototype to explicitly fulfill every requirement and "Real-World Complexity" outlined in the prompt:

| Requirement | How CauseTrace Delivers |
| :--- | :--- |
| **1. Detects & Prioritizes** | Uses **STL decomposition** and weekday-paired Z-scores to separate trend from seasonality. We rank by statistical urgency, ignoring calendar noise. |
| **2. Heterogeneous Sources** | Reconciles 3 different grains & SLAs: Daily POS (24h SLA), Weekly Marketing spend (168h SLA), and Monthly unstructured Support Tickets (720h SLA). |
| **3. Identifies Drivers** | Rejects basic correlation in favor of **Layered Difference-in-Differences (DiD)** causal inference and exact arithmetic **Waterfall decomposition**. |
| **4. Persona Narratives** | Generates CFO Briefs (high-level stories) and Analyst Tables (deep mathematical dives). Enforced by the `ALL NUMBERS TRACED` registry badge. |
| **5. Abstains on Uncertainty** | Our **Contradiction Guard** dynamically drops confidence and quarantines the LLM if evidence is missing or conflicts (e.g., margins rise while sentiment crashes). |
| **6. Action Recommendations** | Maps drivers to *Controllable Levers*, calculating expected $ impact (via waterfall reversal), assigning owners, and setting monitoring plans. |
| **7. Continuous Learning** | **Human-in-the-Loop** feedback dropdown maps analyst corrections to our semantic ontology, saving to DuckDB to permanently tune live confidence weights. |
| **8. Realistic Constraints** | Sub-50ms latency via in-memory DuckDB. Built-in **Telemetry** tracks every token, LLM call, and fraction of a cent. **Drift Monitoring (PSI)** alerts engineers to re-fit baselines. |

---

## 🧠 Core Architecture (The "One-Slide" View)

CauseTrace isolates the Language Model to Stage 8. It relies on a rigorous 9-stage data engineering pipeline governed by a **Semantic Contract** (`kpi_contracts.json`).

```text
 CSV sources ──► 1 load_and_validate ──► 2 semantic_resolver (RBAC)
                                              │
              3 detection_engine ◄────────────┘  STL + paired-weekday z/pct gates
                     │                           scipy p-values | fallback: rolling z
               4 decomposition_engine            exact price/volume/mix/returns
                     │                           waterfall (identity asserted)
              5 causal_engine                    layered DiD A/B/C/D/E tests
                     │                           clarity from control agreement
              6 confidence_engine                weighted composite + tiers +
                     │                           contradiction guard -> ABSTAIN
              7 action_engine                    driver -> lever -> action ->
                     │                           impact -> owner -> monitoring
              8 llm_narrative  ◄── ONLY LLM STEP strict JSON, anti-hallucination
                     │                           validator + response cache
              9 persona_router                   CFO brief vs analyst tables
```

## 🌟 Key Differentiators & Real-World Complexities Solved

### 1. Synthetic Data Generation & Ground Truth
We didn't just hardcode a CSV. We engineered `generate_data.py`—a Python-based synthetic world generator that simulates a multi-region retail business. We mathematically planted specific ground-truth events (logistics delays, competitor promos, price elasticity changes) so we could rigorously test our engine's precision and recall.

### 2. The Anti-Hallucination Firewall
Before the LLM is called, the pipeline registers every computed fact. When the LLM returns a narrative, a regex validator checks every number against the Evidence Registry. If the LLM hallucinates or rounds incorrectly, the validation fails, the LLM payload is dropped, and a safe Python template is served instead.

### 3. Exact Arithmetic Waterfall & Layered DiD
KPIs have interacting variables. We don't guess.

- **Waterfall:** We compute an exact Price, Volume, Mix, and Return breakdown to attribute the exact dollar impact of a movement.
- **Layered DiD:** We use multi-control group Difference-in-Differences tests to mathematically isolate overlapping shocks (e.g., using the North region as a control to isolate a Competitor Promo in the South, then subtracting a known Price Elasticity effect to find the true impact).

### 4. Sparse History Handling
For newly launched products (e.g., SKU-NEW-01 with only 21 days of data), the engine recognizes that STL requires 4 seasonal cycles. Instead of breaking, it safely degrades to a rolling Z-score fallback.

### 5. Enterprise Security (RBAC)
Switching to the Analyst persona triggers Row and Column-Level Security. The workspace locks to the Analyst's specific region, and sensitive metrics (like `unit_cost`) are physically masked from the payload.

### 6. Text-to-Math Reconciliation
We process unstructured text (support tickets) deterministically. We calculate a Sentiment Z-score drift. Only if the math proves a significant drop in sentiment do we use a keyword retrieval stage to pull the exact contextual ticket snippet to augment the LLM's prompt.

---

## 🚀 Quickstart & Setup Guide

### Prerequisites
- Python 3.10+
- Node.js 18+

### 1. Environment Configuration
Create a `.env` file in the root directory:

```bash
cp .env.example .env
```

Ensure the following variables are set in your `.env` file:

```ini
# Freezes the demo clock so pipeline SLAs compute accurately against the generated data.
DEMO_NOW=2026-07-30

# We recommend using 'mock' for local evaluation to guarantee 100% deterministic,
# zero-latency narration without needing an API key.
# To use a live LLM, change this to 'gemini' and provide a GEMINI_API_KEY.
LLM_PROVIDER=mock
```

### 2. Backend Setup (FastAPI + DuckDB)
Open a terminal in the root directory:

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Generate the synthetic world (seeded for absolute reproducibility)
python generate_data.py --scenario default

# Start the API
python -m uvicorn app.main:app --port 8000
```

### 3. Frontend Setup (React + Vite)
Open a second terminal:

```bash
cd web
npm install
npm run dev
```

The application will be running at http://localhost:5173.

---

## 🧪 Chaos Testing Guide (For Judges)

We built a live chaos-injection script to prove our pipeline reacts to real-world data pipeline failures dynamically, rather than relying on hardcoded UI mockups.

With the app running, open a third terminal in the root directory:

### Test 1: The Missing Data Abstention

```bash
python judge_test.py --test missing_data
```

**What to watch:** Hard refresh the browser (Ctrl + Shift + R). The dashboard won't crash. Instead, the CAC card and CFO narrative will throw a red `VALIDATION FAILED` or amber `ANALYSIS WITHHELD` badge.

**Why it matters:** Because the marketing spend data vanished, our Semantic Contract dynamically dropped the composite confidence score below our hardcoded 0.60 threshold. The system physically quarantines the LLM, refuses to guess, and asks a Clarifying Question.

### Test 2: Restore to Baseline

```bash
python judge_test.py --test restore
```

**What to watch:** Hard refresh the browser. The data is restored to the pristine baseline, and the insights return to their fully supported, green states.

*(See `judge_test.py` for additional schema drift and outlier injection scenarios.)*

---

## 📊 Automated Testing

To verify the integrity of the data engineering and causal math pipelines:

```bash
# Run the 14 pipeline phase gates
python -m pytest tests/test_phase2_pipeline.py -q

# Run the full-stack smoke test (36 integration checks)
python tests/test_phase4_integration.py
```
