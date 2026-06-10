"""persona — the ADR-036 continuous persona-concept layer.

An isolated ONA instance learns the user's stable working STYLE/FOCUS from bounded, idle-gated batches
of events; truths are checkpointed to SQLite; and the LLM system prompt is injected from SQLite (fast,
no ONA on the hot path) through a CLOSED, developer-curated vocabulary. Distinct from the Habit Brain
(behavioral/action); persona is semantic/style. Never gates an action — it only shapes the prompt.

Public interface (ADR-001: a module's surface is its `__init__.py` + `__all__`).
"""
from .extract import extract, parse_items
from .store import PersonaStore
from .vocab import VOCAB, catalog_for_prompt, is_known, phrase_for, render_persona, term

__all__ = ["PersonaStore", "extract", "parse_items", "VOCAB", "catalog_for_prompt", "is_known",
           "phrase_for", "render_persona", "term"]
