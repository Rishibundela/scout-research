import asyncio
import streamlit as st
from agent_client import ScoutAgentClient

st.set_page_config(
    page_title="Scout Research Agent", page_icon="🔍", layout="wide"
)

st.title("🔍 Scout Research Agent Dashboard")
st.caption("Connected to local LangGraph Docker Server (`localhost:8123`)")


# Helper function to execute coroutines safely inside Streamlit
def run_async(coro):
    return asyncio.run(coro)


# Instantiate client directly without caching connection pools
client = ScoutAgentClient()

# Session State for Thread Management & History
if "thread_id" not in st.session_state:
    st.session_state.thread_id = run_async(client.create_thread())
    st.session_state.messages = []

# Sidebar Controls
with st.sidebar:
    st.header("Session Settings")
    st.write(f"**Thread ID:** `{st.session_state.thread_id}`")

    selected_graph = st.selectbox(
        "Select Graph Engine",
        options=[
            "deep_research_agent",
            "research_agent_supervisor",
            "research_agent",
            "scope_research",
        ],
        index=0,
    )

    if st.button("New Session / Clear Chat"):
        st.session_state.thread_id = run_async(client.create_thread())
        st.session_state.messages = []
        st.rerun()

# Render Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# Async helper runner for Streamlit live streaming
async def run_stream(user_input: str, container):
    full_response = ""
    async for event in client.stream_agent_response(
        thread_id=st.session_state.thread_id,
        message=user_input,
        graph_name=selected_graph,
    ):
        node_name = getattr(event, "event", "update")
        data = getattr(event, "data", {})

        if isinstance(data, dict):
            for node, content_obj in data.items():
                if isinstance(content_obj, dict) and "messages" in content_obj:
                    for m in content_obj["messages"]:
                        content = m.get("content", "")
                        if content:
                            full_response += f"\n\n**[{node}]**: {content}"
                            container.markdown(full_response)
                else:
                    full_response += f"\n\n`[Node: {node_name}]` Updated"
                    container.markdown(full_response)

    return (
        full_response
        if full_response
        else "Agent completed execution with no text output."
    )


# User Chat Input
if prompt := st.chat_input("Ask Scout Research Agent..."):
    # Render user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Render Assistant Response with Live Streaming
    with st.chat_message("assistant"):
        response_container = st.empty()
        with st.spinner(f"Agent executing via `{selected_graph}`..."):
            final_output = run_async(run_stream(prompt, response_container))

    # Save Assistant Response to History
    st.session_state.messages.append(
        {"role": "assistant", "content": final_output}
    )