"""
Scout Research Agent — Streamlit Interface
============================================
A production-grade chat UI for a LangGraph-powered research agent,
running 100% database-driven from Supabase via Render using the LangGraph SDK.

Architecture:
- agent_client.py    Thin LangGraph SDK wrapper.
- repository.py      Database repository layer wrapping thread operations.
- agent_runtime.py   Async worker thread execution and bubble grouping.
- report_utils.py    Markdown, KaTeX, and Mermaid iframe rendering & exports.
- app.py (this file) Streamlit UI workspace coordinating sessions and streams.
"""

from __future__ import annotations

import re
import asyncio
import logging
from typing import Any, Optional

import streamlit as st

import agent_runtime
from config import settings
from research_service import ResearchService
from report_utils import build_report_html, export_markdown, export_pdf

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scout.ui")

HAS_FRAGMENT = hasattr(st, "fragment")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
ASSISTANT_AVATAR = "🛰️"
USER_AVATAR = "🧑‍💻"

SUGGESTED_TOPICS = [
    "Summarize the latest advances in solid-state battery technology.",
    "Compare RAG vs long-context LLMs for enterprise knowledge search.",
    "What are the open safety risks in autonomous multi-agent systems?",
    "Survey current approaches to LLM evaluation and benchmarking.",
    "What's the state of the art in on-device / edge AI inference?",
    "Compare LangGraph, CrewAI, and AutoGen for production agent systems.",
]

STAGE_ICONS: dict[str, str] = {
    "clarify": "🤔",
    "general_assistant": "💬",
    "write_research_brief": "🧭",
    "supervisor": "🧠", "orchestrat": "🧠", "route": "🧠",
    "search": "🔍", "retriev": "🔍", "web": "🔍",
    "analy": "🧪",
    "verify": "✅", "valid": "✅",
    "critique": "🔎", "review": "🔎",
    "final_report_generation": "📝", "draft": "📝", "writ": "📝", "report": "📝",
    "output_guardrail": "🛡️",
    "__error_handler__": "♻️",
}

STATUS_LABELS = {
    "running": "🧠 Researching…",
    "done": "✅ Done",
    "interrupt": "🤔 Needs your input",
    "error": "⚠️ Something went wrong",
    "cancelled": "⏹️ Stopped",
}

# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"]  { font-family: 'Inter', sans-serif; }
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

.app-header {
    padding: 1.1rem 1.5rem; border-radius: 16px; margin-bottom: 1.2rem;
    background: linear-gradient(120deg, #0f172a 0%, #1e3a5f 45%, #2563eb 100%);
    box-shadow: 0 8px 24px rgba(37, 99, 235, 0.18);
}
.app-header h1 { color: #fff; font-size: 1.5rem; font-weight: 700; margin: 0; letter-spacing: -0.02em; }
.app-header p { color: #cbd5e1; font-size: 0.9rem; margin: 0.3rem 0 0 0; }

section[data-testid="stSidebar"] { background: #0b1220; border-right: 1px solid rgba(148,163,184,0.15); }
section[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
section[data-testid="stSidebar"] .stButton button {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px; text-align: left;
}
section[data-testid="stSidebar"] .stButton button:hover { background: #2563eb; border-color: #2563eb; color: #fff !important; }

.stChatMessage { border-radius: 14px; }
.thread-id { font-family: monospace; font-size: 0.72rem; color: #94a3b8; background: rgba(148,163,184,0.08); padding: 3px 7px; border-radius: 6px; word-break: break-all; }
.report-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.72rem; font-weight:600; background: rgba(37,99,235,0.1); color:#2563eb; border:1px solid rgba(37,99,235,0.25); margin-bottom:6px; }
</style>
"""

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def run_async(coro):
    return asyncio.run(coro)

def _stage_icon(node_name: str) -> str:
    key = (node_name or "").lower()
    for token, icon in STAGE_ICONS.items():
        if token in key:
            return icon
    return "⚙️"

def _stage_label(node_name: str) -> str:
    name = node_name or ""
    if name.startswith("__error_handler__"):
        return f"Recovering ({name.replace('__error_handler__', '').replace('_', ' ').title()})"
    return name.replace("_", " ").replace("-", " ").title()

def _interrupt_prompt_text(payload: Any) -> str:
    if isinstance(payload, dict):
        msg = payload.get("message") or payload.get("question")
        if msg:
            return str(msg)
    return "I need a bit more information to continue."

def safely(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        logger.exception("UI component failed: %s", fn)
        st.error(f"UI component hit an error: {exc}")
        return None

# --------------------------------------------------------------------------
# Database Thread Lifecycle (using SDK)
# --------------------------------------------------------------------------
def _start_agent_thread() -> str:
    service = ResearchService()
    return run_async(service.start_session(user_id=settings.DEFAULT_USER_ID))

def create_new_chat() -> None:
    with st.spinner("Starting a new conversation…"):
        try:
            thread_id = _start_agent_thread()
            st.session_state.thread_id = thread_id
            st.query_params["thread_id"] = thread_id
            st.session_state.messages = []
            st.session_state.current_report = None
            st.session_state.active_run_id = None
            st.session_state.pending_interrupt = None
            st.session_state.pop("viewer_report", None)
        except Exception as exc:
            st.toast(f"Couldn't start new chat: {exc}", icon="⚠️")
            return
    st.rerun()

def delete_chat(thread_id: str) -> None:
    service = ResearchService()
    with st.spinner("Deleting conversation…"):
        try:
            run_async(service.delete_session(thread_id))
            if st.session_state.get("thread_id") == thread_id:
                st.session_state.thread_id = None
                st.query_params.pop("thread_id", None)
                st.session_state.messages = []
                st.session_state.current_report = None
                st.session_state.active_run_id = None
                st.session_state.pending_interrupt = None
                st.session_state.pop("viewer_report", None)
            st.toast("Conversation deleted", icon="🗑️")
        except Exception as exc:
            st.toast(f"Couldn't delete chat: {exc}", icon="⚠️")
    st.rerun()

def ensure_current_chat() -> str:
    service = ResearchService()
    
    # Try getting the active Thread ID from the URL parameters first (persists on reload!)
    url_thread_id = st.query_params.get("thread_id")
    if url_thread_id:
        st.session_state.thread_id = url_thread_id

    current_id = st.session_state.get("thread_id")

    if current_id:
        # If messages aren't populated yet, load them from Supabase
        if not st.session_state.get("messages"):
            try:
                st.session_state.messages = run_async(service.get_session_history(current_id))
                st.session_state.current_report = run_async(service.get_session_report(current_id))
            except Exception:
                pass
        return current_id

    # Try listing from Supabase
    try:
        sessions = run_async(service.list_sessions(settings.DEFAULT_USER_ID))
        if sessions:
            st.session_state.thread_id = sessions[0]["thread_id"]
            st.query_params["thread_id"] = sessions[0]["thread_id"]
            st.session_state.messages = run_async(service.get_session_history(st.session_state.thread_id))
            st.session_state.current_report = run_async(service.get_session_report(st.session_state.thread_id))
            return st.session_state.thread_id
    except Exception as exc:
        logger.error(f"Error loading thread list from Supabase: {exc}")

    # Fallback to creating a new thread
    thread_id = _start_agent_thread()
    st.session_state.thread_id = thread_id
    st.query_params["thread_id"] = thread_id
    st.session_state.messages = []
    st.session_state.current_report = None
    return thread_id

# --------------------------------------------------------------------------
# Sidebar & Connection tests
# --------------------------------------------------------------------------
def render_sidebar(thread_id: str) -> None:
    service = ResearchService()
    with st.sidebar:
        st.markdown("### 🛰️ Scout")
        st.caption(settings.APP_TITLE)
        st.divider()

        if st.button("🆕  New chat", use_container_width=True):
            create_new_chat()

        st.divider()
        st.markdown("**Chats**")
        
        # Load thread list directly from Supabase via service
        try:
            sessions = run_async(service.list_sessions(settings.DEFAULT_USER_ID))
        except Exception as e:
            logger.error(f"Failed listing sessions from Supabase: {e}")
            sessions = []

        if not sessions:
            st.caption("No chats yet.")
        for s in sessions:
            is_active = s["thread_id"] == thread_id
            row = st.container()
            with row:
                cols = st.columns([5, 1])
                label = ("🟢 " if is_active else "") + (s["title"] or f"Chat {s['thread_id'][:8]}")
                if cols[0].button(label, key=f"chat_{s['thread_id']}", use_container_width=True):
                    st.session_state.thread_id = s["thread_id"]
                    st.query_params["thread_id"] = s["thread_id"]
                    st.session_state.messages = run_async(service.get_session_history(s["thread_id"]))
                    st.session_state.current_report = run_async(service.get_session_report(s["thread_id"]))
                    st.session_state.pending_interrupt = None
                    st.session_state.pop("viewer_report", None)
                    st.rerun()
                if cols[1].button("🗑️", key=f"del_{s['thread_id']}", help="Delete this chat"):
                    delete_chat(s["thread_id"])

        st.divider()
        with st.expander("⚙️ Session details"):
            st.markdown(f"**Assistant:** `{settings.ASSISTANT_ID}`")
            st.markdown(f"**Server:** `{settings.RENDER_URL}`")
            st.markdown("**Thread ID**")
            st.markdown(f'<div class="thread-id">{thread_id}</div>', unsafe_allow_html=True)

        if st.button("🔌  Test connection", use_container_width=True):
            _test_connection()

def _test_connection() -> None:
    async def _ping() -> None:
        service = ResearchService()
        tid = await service.start_session(user_id="__connection_test__")
        await service.close_session(tid)

    with st.spinner("Pinging Scout server…"):
        try:
            run_async(_ping())
            st.toast("Connected successfully", icon="✅")
        except Exception as exc:
            st.toast(f"Connection failed: {exc}", icon="🔴")

# --------------------------------------------------------------------------
# Message / Report Rendering
# --------------------------------------------------------------------------
def is_report(content: str) -> bool:
    if not content:
        return False
    if len(content) > 1500:
        return True
    lower_content = content.lower()
    if "here is the final report" in lower_content or "here is the finalized research report" in lower_content:
        return True
    if "\n## " in content or "\n### " in content or content.startswith("# "):
        return True
    return False

def render_copy_button(text: str, key: str) -> None:
    """Renders a small client-side button to copy text to the clipboard."""
    import json
    safe_text = json.dumps(text)
    html_code = f"""
    <button id="copy-btn" style="
        width: 100%;
        background-color: #ffffff;
        color: #0f172a;
        border: 1px solid #e2e8f0;
        padding: 5px 10px;
        border-radius: 8px;
        font-family: sans-serif;
        font-size: 14px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        transition: all 0.2s;
        height: 38px;
    ">
        📋 Copy Report
    </button>
    <script>
        const btn = document.getElementById('copy-btn');
        btn.addEventListener('click', () => {{
            const textToCopy = {safe_text};
            navigator.clipboard.writeText(textToCopy).then(() => {{
                btn.style.backgroundColor = '#22c55e';
                btn.style.color = '#ffffff';
                btn.style.borderColor = '#22c55e';
                btn.innerHTML = '✅ Copied!';
                setTimeout(() => {{
                    btn.style.backgroundColor = '#ffffff';
                    btn.style.color = '#0f172a';
                    btn.style.borderColor = '#e2e8f0';
                    btn.innerHTML = '📋 Copy Report';
                }}, 2000);
            }}).catch((err) => {{
                alert('Could not copy text: ' + err);
            }});
        }});
        btn.addEventListener('mouseover', () => {{
            btn.style.borderColor = '#2563eb';
            btn.style.color = '#2563eb';
        }});
        btn.addEventListener('mouseout', () => {{
            btn.style.borderColor = '#e2e8f0';
            btn.style.color = '#0f172a';
        }});
    </script>
    """
    st.components.v1.html(html_code, height=45)

def render_message(msg: dict, idx: int) -> None:
    avatar = USER_AVATAR if msg["role"] == "user" else ASSISTANT_AVATAR
    with st.chat_message(msg["role"], avatar=avatar):
        is_report_content = msg["role"] == "assistant" and is_report(msg["content"])
        if is_report_content:
            st.markdown('<span class="report-badge">📄 Final report</span>', unsafe_allow_html=True)
            with st.expander("📖 View Full Research Report", expanded=True):
                html_code = build_report_html(msg["content"])
                st.components.v1.html(html_code, height=650, scrolling=True)
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    render_copy_button(msg["content"], key=f"copy_{st.session_state.thread_id}_{idx}")
                c2.download_button(
                    "⬇️ Download Markdown (.md)", data=export_markdown(msg["content"]),
                    file_name="research_report.md", mime="text/markdown",
                    key=f"md_{st.session_state.thread_id}_{idx}", use_container_width=True,
                )
                pdf_bytes = export_pdf(msg["content"], title="Research Report")
                if pdf_bytes:
                    c3.download_button(
                        "⬇️ Download PDF (.pdf)", data=pdf_bytes, file_name="research_report.pdf",
                        mime="application/pdf", key=f"pdf_{st.session_state.thread_id}_{idx}", use_container_width=True,
                    )
                else:
                    c3.caption("PDF export unavailable")
        else:
            st.markdown(msg["content"])

        if msg.get("trace"):
            with st.expander(f"🧩 {len(msg['trace'])} step(s)", expanded=False):
                for step in msg["trace"]:
                    st.markdown(f"{_stage_icon(step)} {_stage_label(step)}")

# --------------------------------------------------------------------------
# Clarification Forms & Checking interrupts
# --------------------------------------------------------------------------
def render_clarification_form(thread_id: str) -> None:
    payload = st.session_state.pending_interrupt
    if not payload:
        return

    questions = payload.get("questions") if isinstance(payload, dict) else None

    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        st.markdown(f"🤔 {_interrupt_prompt_text(payload)}")

    with st.form("clarify_form", clear_on_submit=True, border=True):
        answers: dict[str, str] = {}
        if isinstance(questions, list) and questions:
            for i, q in enumerate(questions):
                if isinstance(q, dict):
                    qid = str(q.get("id", i))
                    qtext = q.get("question") or q.get("text") or f"Question {i + 1}"
                else:
                    qid, qtext = str(i), str(q)
                answers[qid] = st.text_input(qtext, key=f"clarify_{qid}")
        else:
            answers["response"] = st.text_area("Your answer", key="clarify_free_text")

        submitted = st.form_submit_button("↩️  Submit and continue", type="primary", use_container_width=True)

    if submitted:
        resume_value = answers if (isinstance(questions, list) and questions) else answers.get("response", "")
        summary = "\n".join(f"- **{k}:** {v}" for k, v in answers.items() if v) or "_(submitted)_"

        st.session_state.pending_interrupt = None
        st.session_state.messages.append({"role": "user", "content": summary})

        # Kick off background resume run
        handle = agent_runtime.start_run("resume", thread_id, resume_value=resume_value)
        st.session_state.active_run_id = handle.run_id
        st.rerun()

# --------------------------------------------------------------------------
# Background Live Runs (polling and finalizing)
# --------------------------------------------------------------------------
def _render_run_snapshot(snap) -> None:
    if snap.progress:
        label = STATUS_LABELS.get(snap.status, "Working…")
        with st.status(label, expanded=(snap.status == "running")):
            for node in snap.progress:
                st.markdown(f"{_stage_icon(node)} {_stage_label(node)}")

    if snap.segments and not snap.segments[-1].finalized:
        seg = snap.segments[-1]
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            cursor = "▌" if snap.status == "running" else ""
            st.markdown((seg.text or "_Thinking…_") + cursor)

    if snap.status == "error" and snap.error:
        st.error(f"⚠️ {snap.error}")
    elif snap.status == "cancelled":
        st.info("⏹️ Stopped. You can resume from the last checkpoint below, or start a new query.")

def _finalize_run(thread_id: str, run_id: str, snap) -> None:
    service = ResearchService()
    st.session_state.active_run_id = None

    if snap.status == "interrupt":
        st.session_state.pending_interrupt = snap.interrupt_payload
    elif snap.status == "error":
        st.session_state.messages.append({"role": "assistant", "content": f"⚠️ {snap.error}"})

    # Pull refreshed history and final report from Supabase
    st.session_state.messages = run_async(service.get_session_history(thread_id))
    st.session_state.current_report = run_async(service.get_session_report(thread_id))

    agent_runtime.forget_run(run_id)
    st.rerun()

if HAS_FRAGMENT:
    @st.fragment(run_every=settings.POLL_INTERVAL_SECONDS)
    def render_live_run(thread_id: str, run_id: str) -> None:
        handle = agent_runtime.get_run(run_id)
        if handle is None:
            return
        snap = handle.snapshot()
        _render_run_snapshot(snap)

        if st.button("⏹️ Stop", key=f"stop_{run_id}"):
            agent_runtime.request_cancel(run_id)
            st.toast("Stopping…", icon="⏹️")

        if snap.status != "running":
            _finalize_run(thread_id, run_id, snap)
else:
    def render_live_run(thread_id: str, run_id: str) -> None:
        handle = agent_runtime.get_run(run_id)
        if handle is None:
            return
        import time
        while True:
            snap = handle.snapshot()
            if snap.status != "running":
                break
            time.sleep(settings.POLL_INTERVAL_SECONDS)
        _render_run_snapshot(snap)
        _finalize_run(thread_id, run_id, snap)

def render_orphaned_run_banner(thread_id: str) -> None:
    active_run_id = st.session_state.get("active_run_id")
    if not active_run_id or agent_runtime.get_run(active_run_id) is not None:
        return
    st.warning("A previous run on this thread didn't finish (the app may have restarted).")
    if st.button("▶️ Resume from last checkpoint", key="resume_orphaned"):
        handle = agent_runtime.start_run("checkpoint", thread_id)
        st.session_state.active_run_id = handle.run_id
        st.rerun()
    if st.button("Discard and start fresh", key="discard_orphaned"):
        st.session_state.active_run_id = None
        st.rerun()

# --------------------------------------------------------------------------
# Submitting new queries
# --------------------------------------------------------------------------
def submit_query(thread_id: str, query: str) -> None:
    query = (query or "").strip()
    if not query:
        return
    
    service = ResearchService()
    
    # Auto-rename thread title based on first query
    try:
        sessions = run_async(service.list_sessions(settings.DEFAULT_USER_ID))
        matching = [s for s in sessions if s["thread_id"] == thread_id]
        if matching and (matching[0]["title"] == "New chat" or not matching[0]["title"]):
            run_async(service.update_session_title(thread_id, query[:25]))
    except Exception as e:
        logger.warning(f"Could not auto-update thread title metadata: {e}")

    # Clear prior UI state variables
    st.session_state.messages.append({"role": "user", "content": query})
    st.session_state.current_report = None
    st.session_state.pending_interrupt = None

    handle = agent_runtime.start_run("execute", thread_id, query=query)
    st.session_state.active_run_id = handle.run_id
    st.rerun()

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title=settings.APP_TITLE, page_icon="🛰️", layout="wide", initial_sidebar_state="expanded",
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    try:
        thread_id = ensure_current_chat()
    except Exception as exc:
        st.markdown(f'<div class="app-header"><h1>🛰️ {settings.APP_TITLE}</h1><p>Connection trouble</p></div>', unsafe_allow_html=True)
        st.error(f"Couldn't reach the agent server at `{settings.RENDER_URL}`.\n\n**Details:** {exc}")
        if st.button("Retry"):
            st.rerun()
        st.stop()

    safely(render_sidebar, thread_id)

    st.markdown(
        f"""<div class="app-header"><h1>🛰️ {settings.APP_TITLE}</h1>
        <p>Real-time, tool-using research agent — streaming answers with a live reasoning trace.</p></div>""",
        unsafe_allow_html=True,
    )

    # Render chat content directly in a centered full-width container
    messages = st.session_state.messages
    active_run_id = st.session_state.get("active_run_id")
    pending_interrupt = st.session_state.get("pending_interrupt")

    if not messages and not active_run_id and not pending_interrupt:
        st.info("👋 Ask a research question, or try one of these:")
        cols = st.columns(2)
        for i, topic in enumerate(SUGGESTED_TOPICS):
            if cols[i % 2].button(topic, key=f"topic_{i}", use_container_width=True):
                submit_query(thread_id, topic)

    for i, msg in enumerate(messages):
        safely(render_message, msg, i)

    safely(render_orphaned_run_banner, thread_id)

    if pending_interrupt:
        safely(render_clarification_form, thread_id)
    elif active_run_id:
        safely(render_live_run, thread_id, active_run_id)

    disabled = bool(pending_interrupt) or bool(active_run_id)
    user_query = st.chat_input(
        "Ask Scout a research question…" if not disabled else "Please wait for the current turn to finish…",
        disabled=disabled,
    )
    if user_query:
        submit_query(thread_id, user_query)

if __name__ == "__main__":
    main()