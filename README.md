# 🔍 Scout: Autonomous Deep Research Engine

**Scout** is a production-grade, fault-tolerant **Autonomous Deep Research Engine** engineered as a distributed, multi-agent system. Built using **LangGraph**, **Gemini**, and **Docker**, Scout orchestrates parallel research sub-agents, manages persistent PostgreSQL state checkpoints, enforces multi-tier security perimeters, and optimizes token economics across long-running research sessions.

---

## 🌟 Key Features

*   💾 **100% Session Crash Recovery:** Database-driven thread checkpointing via Supabase PostgreSQL guarantees zero lost progress on server restarts, page refreshes, or dropped connections.
*   📊 **High-Fidelity PDF Math & Chemistry Compiler:** Compiles LaTeX equations ($E=mc^2$), scientific matrices, and complex chemical structures ($\text{Li}_6\text{PS}_5\text{Cl}$) into static vector PNGs (via CodeCogs) and embeds them directly inside exported PDFs.
*   📐 **Static Vector Diagrams in PDF:** Automatically renders complex Mermaid.js structural flowcharts, gantt timelines, and sequence charts into static base64 URIs (via `mermaid.ink`) for offline PDF layout compatibility.
*   🔗 **Academic-Grade Citation Deduplicator:** Parses and merges duplicate bibliography links, sequentially re-numbers body citations starting from `[1]` with no gaps, and verifies grounding against raw scraped notes to flag unverified sources.
*   🛡️ **Smart PII & DOI-Aware Redaction:** Scrubs credit card numbers and sensitive keys using issuer-restricted patterns while safely ignoring DOIs, ISBNs, and numeric URL segments.
*   🔀 **State Preservation on Page Reload:** Streamlit session states are dynamically synchronized with URL query parameters (`st.query_params`), allowing active threads to reload instantly when refreshing the browser.

---

## 🏗️ Architecture & Component Map

The project is structured in a modular, decoupled layout separating the frontend visualization from the agent runtime:

```
├── app.py                      # Streamlit UI & frontend page
├── report_utils.py             # Browser renderer & static PDF compilation engine
├── repository.py               # Supabase database session repo
├── research_service.py         # Middle-tier business logic controller
├── agent_client.py             # LangGraph SDK client API wrapper
├── docker-compose.yml          # Container configuration (PostgreSQL, Redis, LangGraph API)
│
├── agent/
│   ├── src/
│   │   ├── graph.py            # Primary state machine compilation
│   │   ├── state.py            # TypedDict state schemas and deduplicating reducers
│   │   ├── prompts.py          # Scoping and report-writing prompt templates
│   │   │
│   │   ├── subgraphs/
│   │   │   ├── scoping_graph.py  # User clarification gate & chitchat assistant
│   │   │   ├── supervisor.py     # Task delegator and parallel research executioner
│   │   │   └── research_graph.py   # Subagent web scraper & fact-summarizer
│   │   │
│   │   └── guardrails/
│   │       ├── output_guard.py   # Citation deduplicator, PII filter, & LaTeX cleaner
│   │       └── topic_classifier.py # Safety category routing
│   │
│   └── DOCKERFILE              # LangGraph API service build blueprint
│
└── tests/                      # Full pytest coverage suite
```

---

## 🛠️ Installation & Setup

### 1. Prerequisites
Ensure you have **Python 3.11+**, **Docker**, and **uv** installed on your system.

### 2. Environment Configuration
Create a `.env` file in the root directory:
```env
GOOGLE_API_KEY=your_gemini_api_key
LANGSMITH_API_KEY=your_optional_langsmith_key
DEFAULT_USER_ID=default_user
```

### 3. Launching Containers (Database & Agent API)
Scout runs on top of a local Supabase PostgreSQL image for state checkpointing and Redis for caching. Start the services using Docker:
```bash
docker compose up -d --build
```
This initializes:
*   **PostgreSQL (Port 5432):** Supabase database instance holding threads, checkpoints, and cron runs.
*   **Redis (Port 6379):** Event store.
*   **LangGraph API (Port 8123):** Asynchronous agent executor.

### 4. Running the Streamlit App
Install local Python dependencies and launch the frontend:
```bash
uv pip install -r pyproject.toml
streamlit run app.py
```
Open `http://localhost:8501` in your browser.

---

## 🧪 Running the Test Suite
Scout comes with automated integration tests covering the API client, research service logic, and guardrails.

To run the test suite:
```bash
uv run pytest
```

---

## 🛡️ Database Schema (Supabase)
Thread checkpoints and sessions reside in the public schema of the `langgraph` database:
*   `public.thread`: Holds thread metadata (including `user_id` inside JSONB), current states, and config.
*   `public.checkpoints`: Holds historical state checkpoints allowing rollback and step resumes.
*   `public.run`: Tracks execution runs and LangSmith session associations.
*   `public.cron`: Handles scheduled recurring tasks.
