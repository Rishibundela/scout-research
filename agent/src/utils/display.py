from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from pathlib import Path
from typing import Union
from langgraph.graph.state import CompiledStateGraph
import json

console = Console()

def format_message_content(message):
    """Convert message content to displayable string"""
    parts = []
    tool_calls_processed = False
    
    # Handle main content
    if isinstance(message.content, str):
        parts.append(message.content)
    elif isinstance(message.content, list):
        # Handle complex content like tool calls (Anthropic format)
        for item in message.content:
            if item.get('type') == 'text':
                parts.append(item['text'])
            elif item.get('type') == 'tool_use':
                parts.append(f"\n🔧 Tool Call: {item['name']}")
                parts.append(f"   Args: {json.dumps(item['input'], indent=2)}")
                parts.append(f"   ID: {item.get('id', 'N/A')}")
                tool_calls_processed = True
    else:
        parts.append(str(message.content))
    
    # Handle tool calls attached to the message (OpenAI format) - only if not already processed
    if not tool_calls_processed and hasattr(message, 'tool_calls') and message.tool_calls:
        for tool_call in message.tool_calls:
            parts.append(f"\n🔧 Tool Call: {tool_call['name']}")
            parts.append(f"   Args: {json.dumps(tool_call['args'], indent=2)}")
            parts.append(f"   ID: {tool_call['id']}")
    
    return "\n".join(parts)


def format_messages(messages):
    """Format and display a list of messages with Rich formatting"""
    for m in messages:
        msg_type = m.__class__.__name__.replace('Message', '')
        content = format_message_content(m)

        if msg_type == 'Human':
            console.print(Panel(content, title="🧑 Human", border_style="blue"))
        elif msg_type == 'Ai':
            console.print(Panel(content, title="🤖 Assistant", border_style="green"))
        elif msg_type == 'Tool':
            console.print(Panel(content, title="🔧 Tool Output", border_style="yellow"))
        else:
            console.print(Panel(content, title=f"📝 {msg_type}", border_style="white"))


def format_message(messages):
    """Alias for format_messages for backward compatibility"""
    return format_messages(messages)


def show_prompt(prompt_text: str, title: str = "Prompt", border_style: str = "blue"):
    """
    Display a prompt with rich formatting and XML tag highlighting.
    
    Args:
        prompt_text: The prompt string to display
        title: Title for the panel (default: "Prompt")
        border_style: Border color style (default: "blue")
    """
    # Create a formatted display of the prompt
    formatted_text = Text(prompt_text)
    formatted_text.highlight_regex(r'<[^>]+>', style="bold blue")  # Highlight XML tags
    formatted_text.highlight_regex(r'##[^#\n]+', style="bold magenta")  # Highlight headers
    formatted_text.highlight_regex(r'###[^#\n]+', style="bold cyan")  # Highlight sub-headers

    # Display in a panel for better presentation
    console.print(Panel(
        formatted_text, 
        title=f"[bold green]{title}[/bold green]",
        border_style=border_style,
        padding=(1, 2)
    ))

def display_graph(graph: CompiledStateGraph) -> None:
    """
    Renders and displays the LangGraph workflow directly in a Jupyter Notebook cell.
    Requires `grandalf` or `pygraphviz` installed, or uses the standard Mermaid API.
    """
    try:
        from IPython.display import Image, display
        display(Image(graph.get_graph().draw_mermaid_png()))
    except Exception as e:
        print(f"Failed to display graph image in notebook: {e}")
        print("\nFallback Mermaid JS Spec:\n")
        print(graph.get_graph().draw_mermaid())


def save_graph_image(graph: CompiledStateGraph, output_path: Union[str, Path] = "graph.png") -> Path:
    """
    Saves the rendered LangGraph architecture diagram to a PNG file.
    
    Args:
        graph: Compiled LangGraph object (`app` or `builder.compile()`)
        output_path: Target image file path (default: "graph.png")
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        png_data = graph.get_graph().draw_mermaid_png()
        with open(path, "wb") as f:
            f.write(png_data)
        print(f"Graph image successfully saved to: {path.resolve()}")
        return path
    except Exception as e:
        print(f"Failed to save PNG image: {e}")
        print("Note: Ensure internet access (for Mermaid API) or install 'pygraphviz'.")
        raise e


def get_mermaid_markdown(graph: CompiledStateGraph) -> str:
    """
    Generates raw Mermaid JS Markdown string.
    Useful for embedding graph visualizer code into README.md or documentation.
    """
    return graph.get_graph().draw_mermaid()