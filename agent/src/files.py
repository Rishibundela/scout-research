from pathlib import Path

def get_current_dir() -> Path:
    """Return the 'src' directory path."""
    # Resolves to: scout-research/agent/src/
    return Path(__file__).resolve().parent.parent