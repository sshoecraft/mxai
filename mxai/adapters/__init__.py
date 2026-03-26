"""Adapter registry for AI backends.

v0.1.1
"""

from .claude import ClaudeAdapter
from .shepherd import ShepherdAdapter

ADAPTERS = {
    "claude": ClaudeAdapter,
    "shepherd": ShepherdAdapter,
}


def get_adapter(backend: str, system_prompt: str, model: str = None,
                effort: str = None, provider: str = None):
    """Create an adapter instance for the given backend."""
    cls = ADAPTERS.get(backend)
    if cls is None:
        available = ", ".join(sorted(ADAPTERS.keys()))
        raise ValueError(f"Unknown backend '{backend}'. Available: {available}")
    return cls(system_prompt, model=model, effort=effort, provider=provider)


def list_backends() -> list:
    """Return list of available backend names."""
    return sorted(ADAPTERS.keys())
