# Architectural Decision Records — Scout Research

This document records the key architectural and technology decisions made during Scout's development. Each entry explains the context, the decision, alternatives considered, and the reasoning.

> **Format:** Each ADR follows the structure: Status → Context → Decision → Alternatives → Reasoning → Tradeoffs

---

## Table of Contents

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](#adr-001-langgraph-as-the-agent-orchestration-framework) | LangGraph as the orchestration framework | ✅ Implemented |
| [ADR-002](#adr-002-gemini-as-the-llm-backbone) | Gemini as the LLM backbone | ✅ Implemented |
| [ADR-003](#adr-003-streamlit-for-the-frontend-ui) | Streamlit for the frontend UI | ✅ Implemented |
| [ADR-004](#adr-004-fastapi-as-the-restsse-api-backend) | FastAPI as the REST/SSE API backend | ✅ Implemented |
| [ADR-005](#adr-005-postgresql-supabase-for-state-persistence) | PostgreSQL (Supabase) for state persistence | ✅ Implemented |
| [ADR-006](#adr-006-supervisor-pattern-for-multi-agent-coordination) | Supervisor pattern for multi-agent coordination | ✅ Implemented |
| [ADR-007](#adr-007-three-tier-security-architecture) | Three-tier security architecture | ✅ Implemented |
| [ADR-008](#adr-008-per-thread-circuit-breakers) | Per-thread circuit breakers | ✅ Implemented |
| [ADR-009](#adr-009-daemon-threads-with-private-event-loops) | Daemon threads with private event loops | ✅ Implemented |
| [ADR-010](#adr-010-url-based-session-persistence) | URL-based session persistence | ✅ Implemented |
| [ADR-011](#adr-011-static-pre-rendering-for-pdf-export) | Static pre-rendering for PDF export | ✅ Implemented |
| [ADR-012](#adr-012-custom-deduplication-reducer) | Custom deduplication reducer | ✅ Implemented |
| [ADR-013](#adr-013-send-to-end-clarification-not-langgraph-interrupts) | Send-to-end clarification flow | ✅ Implemented |
| [ADR-014](#adr-014-multi-layer-url-preservation) | Multi-layer URL preservation | ✅ Implemented |
| [ADR-015](#adr-015-guardrail-node-filtering-and-token-cleaning) | Guardrail node filtering and token cleaning | ✅ Implemented |

---

## ADR-001: LangGraph as the Agent Orchestration Framework

**Status:** ✅ Accepted & Implemented

**Context:** We needed a framework to orchestrate a multi-agent research pipeline with stateful execution, crash recovery, and human-in-the-loop support.

**Decision:** LangGraph

**Alternatives Considered:**

| Framework | Why Rejected |
|---|---|
| **CrewAI** | No native checkpointing to PostgreSQL. Server crash = all progress lost. No compiled state machine model. |
| **AutoGen** | Conversation-based architecture. Lacks persistent checkpointing and native interrupt/resume for human-in-the-loop. |
| **Raw LangChain** | Too low-level. Would need to build state management, checkpointing, graph execution, and retry logic from scratch. |
| **Custom orchestration** | Maximum flexibility but enormous engineering effort for checkpointing, retry, subgraph composition, and streaming. |

**Why LangGraph wins:**
- Automatic PostgreSQL checkpointing — every node transition is persisted. 100% crash recovery for free.
- Compiled state machine model — graphs are validated at compile time, catching wiring errors early.
- Subgraph composition — nest graphs cleanly (orchestrator → supervisor → researcher).
- Token-level streaming out of the box.
- LangSmith integration for full observability.

---

## ADR-002: Gemini as the LLM Backbone

**Status:** ✅ Accepted & Implemented

**Context:** Need a capable LLM for research reasoning, report writing, and classification tasks. Research workloads are token-heavy (50K-100K+ tokens per session).

**Decision:** Google Gemini model family with tiered assignment:

| Model | Role | Why This Tier |
|---|---|---|
| `gemini-3.6-flash` | Report writing, research reasoning | Best quality for complex synthesis |
| `gemini-3.5-flash` | Fallback for report writing | More stable availability |
| `gemini-3.1-flash-lite` | Topic classification | Cheapest, fastest — classification doesn't need heavy reasoning |
| `gemini-3.5-flash-lite` | Fallback for classification | Lightweight backup |

**Alternatives Considered:**

| Model | Why Rejected |
|---|---|
| **GPT-4 / GPT-4o** | Significantly more expensive for high-token workloads. Stricter API rate limits. |
| **Claude** | Strong reasoning but higher cost per token. Less flexible model tier system. |
| **Open-source (Llama, Mistral)** | Requires self-hosting infrastructure. Quality and latency tradeoffs not worth it. |

**Why Gemini:**
- Cost-effective for high-token-volume workloads
- Multiple tiers allow cost optimization per task (use lite for classification, flash for reasoning)
- Fast inference, good structured output support (Pydantic models)
- Generous rate limits

---

## ADR-003: Streamlit for the Frontend UI

**Status:** ✅ Accepted & Implemented — with known tradeoff (see ADR-009)

**Context:** Need a user-facing chat interface for research interactions, with session management, live progress indicators, and rich report rendering.

**Decision:** Streamlit

**Alternatives Considered:**

| Framework | Why Rejected |
|---|---|
| **React / Next.js** | Requires separate frontend build pipeline, API integration, and significantly more development time. |
| **Gradio** | Limited customization for complex chat UIs with session management and multi-format exports. |
| **Chainlit** | Less mature ecosystem. Limited extension points for custom rendering (KaTeX, Mermaid iframes). |

**Why Streamlit:**
- Full UI in a single Python file
- Native chat components (`st.chat_message`, `st.chat_input`)
- Fragment system (`@st.fragment`) enables polling without full page reruns
- Large ecosystem, easy deployment

**Known Tradeoff:** Streamlit's single-threaded script model required building a custom background execution runtime with daemon threads, private asyncio event loops, and thread-safe snapshots. This was the hardest engineering challenge in the project (see ADR-009).

---

## ADR-004: FastAPI as the REST/SSE API Backend

**Status:** ✅ Accepted & Implemented — expanded from 2 to 11 endpoints

**Context:** Need programmatic API access for external clients (mobile apps, Slack bots, CI pipelines) that don't use the Streamlit UI.

**Decision:** FastAPI with Uvicorn — 11 endpoints (8 CRUD + 3 SSE streaming).

**Alternatives Considered:**

| Framework | Why Rejected |
|---|---|
| **Flask** | Synchronous by default. SSE streaming requires workarounds. No native async. |
| **Django REST Framework** | Heavy and opinionated. Overkill for a thin API layer that delegates to the service layer. |
| **gRPC** | Better for service-to-service, but SSE is simpler for browser/client consumption without code generation. |

**Why FastAPI:**
- Native async/await — critical for SSE streaming
- Automatic OpenAPI documentation at `/docs`
- Pydantic integration for request/response validation
- `TestClient` for easy unit testing without a running server
- Lightweight — adds minimal overhead

---

## ADR-005: PostgreSQL (Supabase) for State Persistence

**Status:** ✅ Accepted & Implemented

**Context:** Need durable storage for thread checkpoints, session metadata, and run history.

**Decision:** Supabase-managed PostgreSQL.

**Alternatives Considered:**

| Option | Why Rejected |
|---|---|
| **SQLite** | No concurrent multi-user access. No network access for containerized services. |
| **MongoDB** | LangGraph's checkpoint format is structured — benefits from relational integrity. |
| **Redis only** | Not durable. Server restart = all state lost. |
| **Self-hosted PostgreSQL** | More operational burden. Supabase provides managed hosting, pooling, and a dashboard for free. |

**Why Supabase PostgreSQL:**
- LangGraph has native PostgreSQL checkpoint support
- Managed service with connection pooling
- Free tier sufficient for development
- Dashboard for manual debugging

---

## ADR-006: Supervisor Pattern for Multi-Agent Coordination

**Status:** ✅ Accepted & Implemented

**Context:** Research requires searching multiple topics in parallel and deciding dynamically when enough information has been gathered.

**Decision:** Hierarchical supervisor — delegates to up to 3 parallel research workers, reviews findings, iterates up to 6 times.

**Alternatives Considered:**

| Pattern | Why Rejected |
|---|---|
| **Sequential loop** | "Search N times, then stop." No adaptivity — some topics need 2 searches, others need 10. |
| **Map-reduce** | Fixed workers, no iterative refinement based on findings. |
| **Swarm / peer-to-peer** | Too unpredictable. No central authority to enforce token budgets or iteration limits. |

**Why Supervisor Pattern:**
- **Dynamic delegation** — decides what to research next based on what's already known
- **Structured output** (`ConductResearch` / `ResearchComplete` Pydantic models) makes decisions deterministic
- **`think_tool`** enables meta-cognition — supervisor plans before acting
- **Iteration cap (6)** prevents runaway costs
- **Token-based compaction** triggers at 10K tokens to keep context manageable

---

## ADR-007: Three-Tier Security Architecture

**Status:** ✅ Accepted & Implemented

**Context:** Need to protect against prompt injection, off-topic queries, and sensitive data leakage.

**Decision:** Three independent layers, each catching different threats at different cost/speed points.

| Tier | Component | Speed | What It Catches |
|---|---|---|---|
| 1 | Regex Input Guard | <1ms | Injection patterns (`ignore all previous instructions`, `<\|im_start\|>`) |
| 2 | LLM Topic Classifier | ~200ms | Semantic threats, off-topic queries, harmful content |
| 3 | Output Guardrail | ~100ms | PII/API keys, hallucinated citations, broken LaTeX/Mermaid |

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **System prompt only** | "Soft" defense — users can talk the model out of system prompt instructions. |
| **Single LLM for everything** | Too slow for obvious patterns, too limited for post-generation output cleaning. |
| **External moderation API** | Additional dependency, latency, and cost. Doesn't cover citation verification or LaTeX healing. |

**Why Three Tiers:**
- Each tier is independent — failure of one doesn't compromise others
- Regex is nearly free and catches the majority of injection attempts
- LLM catches semantic threats regex can't detect
- Output guardrail catches problems that no input-stage defense can prevent

---

## ADR-008: Per-Thread Circuit Breakers

**Status:** ✅ Accepted & Implemented — verified by unit test

**Context:** Tavily (web search API) can fail, rate-limit, or timeout. Need fault tolerance without blocking all users.

**Decision:** Per-thread `pybreaker` circuit breaker instances, indexed by `thread_id`.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **Global circuit breaker** | One user's rate limiting blocks ALL users. Unacceptable for multi-tenant. |
| **No circuit breaker (just retry)** | Retrying a dead API wastes time and money. Breaker fails fast after 3 failures. |
| **Per-user circuit breaker** | Better than global, but a user can have multiple threads. Per-thread is the finest useful granularity. |

**Why Per-Thread:**
- Complete tenant isolation
- Fail fast after 3 consecutive failures
- Auto-recovery after 30-second timeout
- Verified by dedicated unit test (`test_multi_tenant_circuit_breaker`)

---

## ADR-009: Daemon Threads with Private Event Loops

**Status:** ✅ Accepted & Implemented

**Context:** Streamlit's single-threaded model blocks the UI during long-running agent calls.

**Decision:** Spawn daemon `threading.Thread` with `asyncio.new_event_loop()`. UI polls `RunHandle.snapshot()` via `@st.fragment(run_every=1.0)`.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **asyncio in main thread** | Conflicts with Streamlit's internal event loop. Runtime errors. |
| **st.spinner / st.status** | Blocks entire page during execution. No streaming, no cancel. |
| **WebSockets** | Streamlit doesn't support custom WebSocket connections. |
| **Separate process** | IPC complexity not worth it. `threading.Lock` is simpler. |
| **Celery / task queue** | Heavy dependency (Redis/RabbitMQ worker). Over-engineered. |

**Why Daemon Threads:**
- Lightweight — no extra infrastructure
- Private event loop avoids Streamlit conflicts
- `threading.Lock` + `snapshot()` = microsecond lock hold times, zero deadlocks
- Daemon threads auto-terminate with main process
- Cancel support via `threading.Event`

---

## ADR-010: URL-Based Session Persistence

**Status:** ✅ Accepted & Implemented

**Context:** Session state needs to survive page refreshes and be shareable via links.

**Decision:** Encode `user_id` and `thread_id` in URL query parameters via `st.query_params`.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **`st.session_state` only** | Lost on page refresh. Can't share links. |
| **Cookies** | Streamlit has limited cookie support. Cross-tab behavior is unpredictable. |
| **Local storage (JavaScript)** | Requires custom HTML components. Fragile. |
| **Server-side session store** | Additional infrastructure for something `st.query_params` does natively. |

**Why URL Query Params:**
- Survives page refresh
- Enables link sharing
- Multiple tabs get different anonymous user IDs
- Zero infrastructure — built into Streamlit

---

## ADR-011: Static Pre-Rendering for PDF Export

**Status:** ✅ Accepted & Implemented — with known tradeoff (SSL bypass for mermaid.ink)

**Context:** Research reports contain math and diagrams. PDFs can't execute JavaScript.

**Decision:** Pre-render via CodeCogs (math) and mermaid.ink (diagrams) into base64 PNG data URIs, then generate PDF with xhtml2pdf.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **Headless Chrome / Puppeteer** | 200MB+ dependency, system-level install, slow, doesn't work in many containers. |
| **WeasyPrint** | Better CSS but still can't render KaTeX/Mermaid without pre-rendering. |
| **Skip math/diagrams in PDF** | Unacceptable. Research reports need formulas and flowcharts. |

**Why API Pre-Rendering:**
- Zero local dependencies beyond Python
- Works everywhere (Docker, Codespaces, local)
- Base64 embedding = fully self-contained PDFs

**Tradeoff:** mermaid.ink requires SSL bypass and User-Agent spoofing. Acceptable for internal tool; needs self-hosted renderer for production scale.

---

## ADR-012: Custom Deduplication Reducer

**Status:** ✅ Accepted & Implemented

**Context:** Parallel research workers may find the same information from the same sources.

**Decision:** Custom `deduplicate_list` LangGraph reducer — merges lists while removing exact string duplicates, preserving insertion order.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **Default list append** | Duplicates accumulate, bloating context and reports. |
| **Set-based dedup** | Loses insertion order. Order matters for chronological flow. |
| **Semantic dedup (embeddings)** | Too expensive per state merge. Exact match catches the common case. |

**Why Custom Reducer:**
- Preserves order, O(n) with set lookup, zero API calls

---

## ADR-013: Send-to-End Clarification (Not LangGraph Interrupts)

**Status:** ✅ Accepted & Implemented

**Context:** When a query is ambiguous, the agent needs to ask for clarification.

**Decision:** Agent sends questions as a regular message, run completes. User's answer starts a new run; agent checks history to decide if clarification is sufficient.

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **LangGraph `__interrupt__`** | Pauses run, holds server resources, pops up modal. Feels unnatural in chat UI. |
| **Separate clarification endpoint** | Over-engineered for a flow that naturally fits chat turns. |

**Why Send-to-End:**
- Feels like natural conversation
- No resources held while waiting
- Each turn is a complete run
- Supports multiple clarification rounds naturally
- User doesn't realize there's a special mechanism

---

## ADR-014: Multi-Layer URL Preservation

**Status:** ✅ Accepted & Implemented — iterated across multiple prompt revisions

**Context:** Source URLs were disappearing at multiple pipeline stages — during research, compaction, supervisor aggregation, and report writing. LLMs also hallucinate URLs from memory.

**Decision:** Explicit URL preservation rules at every stage, plus a "Strict Source Boundary" forbidding the report writer from inventing URLs.

| Stage | Rule |
|---|---|
| Researcher | `<URL Preservation Rule>` — every fact must carry its exact source URL |
| Compaction | `ABSOLUTE URL INTEGRITY` — character-for-character copying |
| Supervisor | `Preserve all embedded https://... URLs` |
| Report Writer | `Strict Source Boundary` — ONLY use URLs from `<Findings>` |
| Output Guardrail | Cross-references citations against raw search results |

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **Post-processing only** | Can catch hallucinated URLs but can't recover URLs lost during compaction. |
| **Structured state fields** | More reliable transport, but LLM still needs to write URLs into report text. |
| **Fine-tuning** | Expensive, slow iteration, doesn't transfer across model versions. |

**Why Multi-Layer:**
- Prevents URL loss at every stage
- Combined with output guardrail = defense-in-depth for citation integrity

---

## ADR-015: Guardrail Node Filtering and Token Cleaning

**Status:** ✅ Accepted & Implemented

**Context:** Internal agent nodes (input_guardrail, intent_classifier) and raw JSON structured output tokens were leaking into the user-facing chat UI.

**Decision:**
- `EXCLUDED_NODES` set filters internal nodes from the UI
- `_clean_clarify_token()` extracts human-readable text from raw JSON tokens
- Active node buffer resets prevent token bleed across bubble groups

**Alternatives Considered:**

| Approach | Why Rejected |
|---|---|
| **Fix at agent level** | Agent should stream everything (needed for LangSmith observability). Filtering is a UI concern. |
| **Post-process entire response** | Adds latency. Real-time regex extraction from the buffer is instant. |

**Why Runtime-Level Filtering:**
- Separation of concerns — agent streams everything, UI filters
- Zero latency penalty
- Observability preserved in LangSmith traces
