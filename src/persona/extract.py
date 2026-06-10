"""Sensory cortex (ADR-036) — Functional Core (S-02). Turn a batch of raw events into validated
Narsese, via the 7B but with a HARD deterministic gate.

The 7B does NOT hand-write Narsese (malformed Narsese crashes the NAR — verified). Instead it returns
a small JSON array of `{predicate, value, freq, conf}` drawn from the CLOSED vocabulary; this code
validates every item against `vocab` and renders the term itself. Anything outside the vocabulary, or
malformed JSON, is dropped — so the ONA engine only ever receives clean, in-vocabulary statements.

`generate(system, user, max_tokens) -> str` is injected (the daemon's LLM, or a fake in tests), so this
stays testable without a model. Never raises.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .vocab import catalog_for_prompt, is_known, term

Generate = Callable[[str, str, int], str]

_PROMPT = (
    "You label evidence of the user's working STYLE and FOCUS, using ONLY this closed vocabulary "
    "(predicate / value):\n{catalog}\n\n"
    "Read the events below and output a JSON array. For each vocabulary pair the events ACTUALLY "
    'evidence, emit {{"predicate": "...", "value": "...", "freq": <0..1>, "conf": <0..1>}} '
    "(freq≈1.0 for clear evidence; conf 0.5–0.9 by strength). Use ONLY pairs from the list above. "
    "If nothing in the vocabulary is evidenced, output []. Output ONLY the JSON array."
)


def _clamp(x: object) -> float:
    try:
        return max(0.0, min(1.0, float(x)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def parse_items(raw: str) -> list[tuple[str, float, float]]:
    """Validate the model's JSON against the closed vocabulary -> [(term, freq, conf)]. Pure."""
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[tuple[str, float, float]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        p, v = str(it.get("predicate", "")).strip(), str(it.get("value", "")).strip()
        if not is_known(p, v):                       # the closed-vocabulary gate
            continue
        out.append((term(p, v), _clamp(it.get("freq", 1.0)), _clamp(it.get("conf", 0.7))))
    return out


def extract(events: list[str], generate: Generate) -> list[tuple[str, float, float]]:
    """One bounded 7B call over a batch of events -> validated in-vocabulary (term, freq, conf) tuples."""
    if not events:
        return []
    user = "Events:\n" + "\n".join(f"- {e}" for e in events)
    try:
        raw = generate(_PROMPT.format(catalog=catalog_for_prompt()), user, 256)
    except Exception:  # noqa: BLE001 — a model hiccup just yields no persona this batch
        return []
    return parse_items(raw)
