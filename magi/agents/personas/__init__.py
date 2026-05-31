"""Persona loader for the MAGI council agents.

Each agent's ADK LlmAgent `instruction` is loaded from the matching markdown file
in this directory. The persona texts are the source material carried over for each
agent; council.py loads them at agent-construction time.
"""

from pathlib import Path

_HERE = Path(__file__).resolve().parent

_FILES = {
    "casper": _HERE / "casper.md",
    "melchior": _HERE / "melchior.md",
    "balthasar": _HERE / "balthasar.md",
}


def load_persona(name: str) -> str:
    """Return the persona text for an agent, or raise if missing/empty.

    Raises:
        KeyError:           unknown agent name.
        FileNotFoundError:  persona file does not exist.
        ValueError:         persona file is empty (guards against constructing an
                            agent with a blank instruction).
    """
    if name not in _FILES:
        raise KeyError(f"unknown persona {name!r}; expected one of {list(_FILES)}")
    path = _FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"persona file missing: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"persona file {path} is empty — populate it before use.")
    return text
