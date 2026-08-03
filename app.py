import asyncio
import os
import uuid
import streamlit as st
from agent_client import ScoutAgentClient
from dotenv import load_dotenv

# 1. Page Config & Setup
load_dotenv()
st.set_page_config(page_title="Scout Research Agent", page_icon="🤖", layout="wide")

# Determine LangGraph Server URL (Streamlit Cloud Secrets -> .env -> Localhost default)
LANGGRAPH_URL = os.getenv("LANGGRAPH_URL", "http://localhost:8123")
if "LANGGRAPH_URL" in st.secrets:
    LANGGRAPH_URL = st.secrets["LANGGRAPH_URL"]

client = ScoutAgentClient(url=LANGGRAPH_URL)


# Helper function to run async generators/coroutines inside Streamlit sync execution
def run_async(coro):
    return asyncio.run(coro)


# 2. Session State Initialization
if "threads" not in st.session_state:
    # Structure: { thread_id: {"name": str, "messages": [{"role": str, "content": str}]} }
    initial_thread_id = str(uuid.uuid4())
    st.session_state.threads = {
        initial_thread_id: {"name": "New Chat", "messages": []}
    }
    st.session_state.current_thread_id = initial_thread_id

if "current_thread_id" not in st.session_state:
    st.session_state.current_thread_id = list(st.session_state.threads.keys())[0]

# Ensure current thread context exists
if st.session_state.current_thread_id not in st.session_state.threads:
    if st.session_state.threads:
        st.session_state.current_thread_id = list(
            st.session_state.threads.keys()
        )[0]
    else:
        new_id = str(uuid.uuid4())
        st.session_state.threads[new_id] = {"name": "New Chat", "messages": []}
        st.session_state.current_thread_id = new_id

current_thread_id = st.session_state.current_thread_id

# 3. Sidebar - Chat Management & Graph Selection
with st.sidebar:
    st.title("⚙️ Workspace")
    st.caption(f"Server: `{LANGGRAPH_URL}`")

    # Select Available Graph Endpoint
    selected_graph = st.selectbox(
        "Select Agent Graph",
        options=[
            "deep_research_agent",
            "scope_research",
            "research_agent",
            "research_agent_supervisor",
        ],
        index=0,
    )

    st.divider()

    # Create New Chat Button
    if st.button("➕ New Chat", use_container_width=True, type="primary"):
        new_thread_id = str(uuid.uuid4())
        st.session_state.threads[new_thread_id] = {
            "name": f"Chat {len(st.session_state.threads) + 1}",
            "messages": [],
        }
        st.session_state.current_thread_id = new_thread_id
        st.rerun()

    st.subheader("💬 Active Threads")

    # List Threads with Selection and Delete Option
    thread_ids_to_delete = []

    for tid, data in list(st.session_state.threads.items()):
        col1, col2 = st.columns([0.8, 0.2])

        # Active thread highlight button
        is_active = tid == current_thread_id
        button_label = f"📍 {data['name']}" if is_active else data["name"]

        with col1:
            if st.button(
                button_label,
                key=f"select_{tid}",
                use_container_width=True,
                disabled=is_active,
            ):
                st.session_state.current_thread_id = tid
                st.rerun()

        with col2:
            # Delete Thread Button
            if st.button("🗑️", key=f"del_{tid}"):
                thread_ids_to_delete.append(tid)

    # Process Thread Deletions
    if thread_ids_to_delete:
        for tid in thread_ids_to_delete:
            # Async call to backend server to prune/delete thread
            run_async(client.delete_thread(tid))
            del st.session_state.threads[tid]

        # Reset current thread pointer if deleted active thread
        if st.session_state.threads:
            st.session_state.current_thread_id = list(
                st.session_state.threads.keys()
            )[0]
        else:
            new_id = str(uuid.uuid4())
            st.session_state.threads[new_id] = {
                "name": "New Chat",
                "messages": [],
            }
            st.session_state.current_thread_id = new_id

        st.rerun()

# 4. Main Chat Interface
st.header(f"🧠 {st.session_state.threads[current_thread_id]['name']}")
st.caption(f"Thread ID: `{current_thread_id}`")

# Render Existing Chat History for Active Thread
active_messages = st.session_state.threads[current_thread_id]["messages"]

for msg in active_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# Async Generator Stream Handler
async def stream_ui(user_prompt: str, container):
    full_response = ""
    async for event in client.stream_agent_response(
        thread_id=current_thread_id,
        message=user_prompt,
        graph_name=selected_graph,
    ):
        # LangGraph values stream mode delivers full state updates in event.data
        if hasattr(event, "data") and isinstance(event.data, dict):
            messages = event.data.get("messages", [])
            if messages:
                latest_msg = messages[-1]
                # Extract content from LangChain Message payload
                if isinstance(latest_msg, dict) and latest_msg.get("type") in [
                    "ai",
                    "assistant",
                ]:
                    full_response = latest_msg.get("content", "")
                    container.markdown(full_response + "▌")
                elif hasattr(latest_msg, "content"):
                    full_response = latest_msg.content
                    container.markdown(full_response + "▌")

    container.markdown(full_response)
    return full_response


# 5. User Input Handler
if prompt := st.chat_input("Ask your agent..."):
    # Append User Message to State & UI
    active_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Update thread title dynamically on first prompt
    if len(active_messages) <= 1:
        st.session_state.threads[current_thread_id]["name"] = (
            prompt[:25] + "..." if len(prompt) > 25 else prompt
        )

    # Stream Assistant Response
    with st.chat_message("assistant"):
        response_container = st.empty()
        with st.spinner("Agent thinking..."):
            final_text = run_async(stream_ui(prompt, response_container))

    # Append Assistant Message to History State
    active_messages.append({"role": "assistant", "content": final_text})
    st.rerun()