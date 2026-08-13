# 🔍 Scout — Autonomous Deep Research Engine

**Scout** orchestrates a team of AI agents that search the web in parallel, synthesize findings, and produce comprehensive research reports — fully autonomously.

Built with **LangGraph** · **Gemini** · **FastAPI** · **Streamlit** · **PostgreSQL** · **Docker**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2+-1C3C3C?logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.60+-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What Makes Scout Different

This isn't a wrapper around an LLM. Scout is a **distributed multi-agent system** with production-grade reliability:

| Capability | How It Works |
|---|---|
| **Multi-Agent Research** | Supervisor delegates to up to 3 parallel research workers via `asyncio.gather`. Each worker searches, reads, and compresses findings independently. |
| **Zero Data Loss** | Every graph node transition is checkpointed to PostgreSQL. Server crash? Browser closed? Resume from the exact node where it stopped. |
| **Three-Tier Security** | Regex injection guard (<1ms) → LLM topic classifier → Output guardrail (PII scrubbing, citation verification, LaTeX/Mermaid auto-healing) |
| **Full REST API** | 11 endpoints — 8 CRUD + 3 SSE streaming. Build mobile apps, Slack bots, or CI integrations against Scout. |
| **Fault Isolation** | Per-thread circuit breakers ensure one user's API failures never block another user's research. |
| **Smart Exports** | PDF reports with pre-rendered math (CodeCogs) and diagrams (mermaid.ink) — no headless browser needed. |

---

## Architecture

```mermaid
graph TD
    subgraph Presentation
        A["Streamlit UI"] 
        B["FastAPI REST + SSE"]
    end

    subgraph Service Layer
        C["ResearchService"]
        D["agent_runtime.py"]
        E["ThreadRepository"]
        F["LangGraphSDKClient"]
    end

    subgraph "Agent Core — Docker"
        G["Input Guard ⚡"]
        H["Topic Classifier 🏷️"]
        GA["General Assistant 💬"]
        I["Scoping & Clarification ❓"]
        J["Research Brief 📝"]
        K["Supervisor 👨‍💼"]
        L["Researcher 1 🔍"]
        M["Researcher 2 🔍"]
        N["Researcher 3 🔍"]
        O["Final Report Writer 📄"]
        P["Output Guardrail 🛡️"]
    end

    subgraph Infrastructure
        Q["PostgreSQL — Supabase"]
        R["Redis"]
        S["Tavily Search API"]
    end

    A --> C
    B --> C
    C --> D
    C --> E
    E --> F
    F --> G
    G --> H
    H -->|"research"| I
    H -->|"chitchat"| GA
    I --> J
    J --> K
    K --> L
    K --> M
    K --> N
    L --> K
    M --> K
    N --> K
    K --> O
    O --> P
    F -.-> Q
    F -.-> R
    L -.-> S
    M -.-> S
    N -.-> S
```

**Three fully decoupled layers** — swap Streamlit for React without touching a single line of agent code.

---

## Technical Highlights

These are the hard engineering problems behind Scout:

<details>
<summary><b>🧵 Streamlit Threading Solution</b> — Background execution without freezing the UI</summary>
<br>

Streamlit re-runs the entire script on every user interaction. Long-running agent calls would freeze the UI.

**Solution:** Daemon threads with private `asyncio` event loops + thread-safe `RunHandle.snapshot()`:
- Each agent run spawns a background daemon thread with its own `asyncio.new_event_loop()`
- The thread accumulates results into a `RunHandle` protected by `threading.Lock`
- The UI polls `RunHandle.snapshot()` every second via `@st.fragment(run_every=1.0)`
- Shallow copy under lock = microsecond lock hold times, zero deadlocks

Internal guardrail nodes are filtered from the UI via `EXCLUDED_NODES`, and raw JSON structured output tokens are cleaned into human-readable text via `_clean_clarify_token()`.
</details>

<details>
<summary><b>🔗 Multi-Layer URL Preservation</b> — Stopping LLMs from hallucinating citations</summary>
<br>

LLMs hallucinate URLs. They'll confidently cite papers that don't exist. URLs were also being lost during context compaction and supervisor aggregation.

**Solution:** Explicit URL preservation rules at every pipeline stage:

| Stage | Enforcement |
|---|---|
| Research Worker | `<URL Preservation Rule>` — every fact must carry its source URL |
| Context Compaction | `ABSOLUTE URL INTEGRITY` — character-for-character URL copying |
| Supervisor | `Preserve all embedded https://... URLs` in scaling rules |
| Report Writer | `Strict Source Boundary` — can ONLY use URLs from `<Findings>`, never from memory |
| Output Guardrail | Cross-references every `[n]` citation against raw search results |

</details>

<details>
<summary><b>⚡ Per-Thread Circuit Breakers</b> — Multi-tenant fault isolation</summary>
<br>

Each thread gets its own `pybreaker` circuit breaker instance. If User A's research triggers Tavily rate limits, only User A's breaker opens. User B's research continues uninterrupted.

```python
def get_tavily_circuit_breaker(thread_id: str):
    if thread_id not in _breakers:
        _breakers[thread_id] = CircuitBreaker(fail_max=3, reset_timeout=30)
    return _breakers[thread_id]
```
</details>

<details>
<summary><b>🛡️ Three-Tier Security</b> — Defense in depth</summary>
<br>

| Tier | Component | Speed | Catches |
|---|---|---|---|
| 1 | Regex Input Guard | <1ms | Prompt injection patterns (`ignore all previous instructions`, `<\|im_start\|>`) |
| 2 | LLM Topic Classifier | ~200ms | Semantic threats, off-topic queries, harmful content |
| 3 | Output Guardrail | ~100ms | PII/API key leakage, hallucinated citations, broken LaTeX/Mermaid syntax |

Each tier is independent — failure of one doesn't compromise the others.
</details>

<details>
<summary><b>📦 Token-Aware Context Compaction</b> — Cost control for long research sessions</summary>
<br>

Token estimation heuristics (0.75 tokens/char for prose, 0.85 for JSON) trigger automatic compaction when research notes exceed ~10,000 tokens. The LLM summarizes notes while preserving key facts and exact source URLs.

Without compaction, a 6-iteration supervisor run with 3 workers could burn 100K+ tokens per request.
</details>

---

## Project Structure

```
scout-research/
├── app.py                      # Streamlit chat UI
├── main.py                     # FastAPI REST + SSE API (11 endpoints)
├── config.py                   # Centralized settings
├── research_service.py         # Business logic controller
├── agent_runtime.py            # Background execution engine
├── agent_client.py             # LangGraph SDK wrapper
├── repository.py               # Database access layer
├── report_utils.py             # HTML/PDF/Markdown export engine
│
├── agent/                      # Agent core (runs in Docker)
│   ├── langgraph.json          # Graph route configuration
│   ├── DOCKERFILE
│   └── src/
│       ├── graph.py            # Main orchestrator graph
│       ├── state.py            # State schemas + dedup reducer
│       ├── schemas.py          # Pydantic structured outputs
│       ├── prompts.py          # Prompt templates
│       ├── subgraphs/
│       │   ├── scoping_graph.py    # Clarification & brief
│       │   ├── supervisor.py       # Multi-agent supervisor
│       │   └── research_graph.py   # Research worker
│       ├── guardrails/
│       │   ├── input_guard.py      # Regex injection blocker
│       │   ├── topic_classifier.py # LLM topic router
│       │   └── output_guard.py     # PII/citation/LaTeX/Mermaid guard
│       └── utils/
│           ├── compaction.py       # Token estimation & compaction
│           ├── search.py           # Tavily + circuit breakers
│           └── helper.py           # Utilities
│
├── tests/                      # Automated test suite
│   ├── test_agent_client.py    # SDK client unit tests
│   ├── test_fixes.py           # Regression tests
│   ├── test_main_api.py        # FastAPI endpoint tests
│   └── test_research_service.py
│
├── notebooks/                  # Jupyter prototyping (6 notebooks)
├── docker-compose.yml          # PostgreSQL + Redis + LangGraph API
├── pyproject.toml              # Dependencies & build config
└── decisions.md                # Architectural Decision Records
```

---

## API Reference

### Session Management

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/research/session` | Create a new research session |
| `GET` | `/api/research/sessions` | List sessions for a user |
| `DELETE` | `/api/research/{thread_id}` | Delete a session |
| `PATCH` | `/api/research/{thread_id}/title` | Update session title |
| `GET` | `/api/research/{thread_id}/state` | Get raw thread state |
| `GET` | `/api/research/{thread_id}/history` | Get formatted message history |
| `GET` | `/api/research/{thread_id}/report` | Get the final research report |
| `POST` | `/api/research/{thread_id}/cancel` | Cancel an active run |

### SSE Streaming

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/research/stream` | Start research — streams `session`, `stage`, `token`, `interrupt`, `complete`, `done` events |
| `POST` | `/api/research/{thread_id}/resume` | Resume from clarification with user answers |
| `POST` | `/api/research/{thread_id}/resume-checkpoint` | Resume from last checkpoint (crash recovery) |

All streaming endpoints emit Server-Sent Events with node-level context:

```
event: token
data: {"node": "write_report", "text": "The research shows..."}

event: interrupt
data: {"node": "clarify_with_user", "data": {"question": "Which time period?"}}
```

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **Docker** & **Docker Compose**
- **uv** package manager

### 1. Environment Configuration

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your_gemini_api_key
TAVILY_API_KEY=your_tavily_api_key
LANGSMITH_API_KEY=your_langsmith_key          # optional
LANGSMITH_TRACING=true                        # optional
DATABASE_URI=postgresql://user:pass@host/db   # Supabase connection string
```

### 2. Start Infrastructure

```bash
docker compose up -d --build
```

This launches:
- **PostgreSQL** (port 5432) — State checkpoints & thread storage
- **Redis** (port 6379) — Run queue & pub/sub
- **LangGraph API** (port 8123) — Agent executor

### 3. Run the Application

**Streamlit UI:**
```bash
uv pip install -r pyproject.toml
streamlit run app.py
```
Open `http://localhost:8501`

**FastAPI Server:**
```bash
uvicorn main:app --reload
```
Open `http://localhost:8000/docs` for the interactive API docs.

---

## Testing

```bash
# Run all unit tests
uv run pytest

# Run integration tests against live deployment
uv run pytest -m integration

# Run only API endpoint tests
uv run pytest tests/test_main_api.py -v
```

Test coverage includes:
- **Unit tests** — SDK client mocking, service layer callbacks, interrupt normalization
- **Regression tests** — Wikipedia URL parentheses, circuit breaker isolation, token estimation accuracy
- **API tests** — All 11 FastAPI endpoints with import-time SDK patching
- **Integration tests** — Full research lifecycle against live Render deployment

---

## Model Strategy

| Component | Primary | Fallback | Purpose |
|---|---|---|---|
| Report Writer | `gemini-3.6-flash` | `gemini-3.5-flash` | Deep reasoning & report synthesis |
| Research Workers | `gemini-3.6-flash` | Retry (3x) | Web search & fact extraction |
| Topic Classifier | `gemini-3.1-flash-lite` | `gemini-3.5-flash-lite` | Fast, cheap query classification |

Every graph node has: **3 retries** with exponential backoff, **180s timeout**, and a **top-level error handler** for graceful degradation.

---

## Architectural Decisions

See **[decisions.md](decisions.md)** for the complete Architectural Decision Record covering:

- Why LangGraph over CrewAI/AutoGen
- Why daemon threads instead of asyncio in Streamlit's main thread
- Why per-thread circuit breakers instead of global
- Why send-to-end clarification instead of LangGraph interrupts
- Why static API pre-rendering instead of headless Chrome for PDFs
- And 10 more engineering decisions with alternatives considered

---

## Database Schema

Thread checkpoints and sessions reside in the Supabase `langgraph` database:

| Table | Purpose |
|---|---|
| `public.thread` | Thread metadata, `user_id` (JSONB), current state, config |
| `public.checkpoints` | Historical state checkpoints for rollback & resume |
| `public.run` | Execution runs & LangSmith session associations |
| `public.cron` | Scheduled recurring tasks |

---

## Dev Environment

**GitHub Codespaces / VS Code Dev Container** — fully configured:
- Python 3.11 (Bookworm)
- Auto-installs dependencies
- Port-forwards Streamlit (8501)
- Auto-launches on attach

Open in Codespaces and start contributing in under 2 minutes.

---

<p align="center">
  Built by <a href="https://github.com/Rishibundela">Rishi Bundela</a>
</p>
