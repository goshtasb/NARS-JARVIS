"""Sync between ONA (L1) and the SQLite system-of-record (L2). Imperative Shell (S-02).

Implements the approved model (no eviction callback exists or is needed):
- write-through  : done at ingestion via store.upsert (see jarvis.Jarvis.learn)
- observe        : parse ONA's Revised/Derived stream -> upsert updated truth
- snapshot       : parse ONA's *concepts dump -> reconcile usage + truth
- cache-miss reload: load pinned + relevant facts from L2 back into a (possibly fresh) L1
"""
from __future__ import annotations

import json
import time

from brain.parse import parse_line

from .fact import to_statement
from .store import MemoryStore


def observe(store: MemoryStore, lines: list[str], now: float | None = None) -> int:
    """Persist truth updates from ONA's Derived/Revised output lines. Returns count updated."""
    now = time.time() if now is None else now
    count = 0
    for line in lines:
        if line.startswith("Revised:") or line.startswith("Derived:"):
            event = parse_line(line)
            if event is not None and event.truth is not None:
                store.upsert(event.term, event.truth.frequency, event.truth.confidence, now=now)
                count += 1
    return count


def parse_concepts(lines: list[str]) -> list[tuple[str, int, int, float, float]]:
    """Parse ONA's *concepts dump into (term, use_count, last_used, freq, conf). Pure.

    Each concept line looks like:  //<a --> b>: { "useCount": 7, "frequency": 1.0, ... }
    """
    out: list[tuple[str, int, int, float, float]] = []
    for line in lines:
        if line.startswith("//<") and ": {" in line:
            term, rest = line[2:].split(": {", 1)
            try:
                data = json.loads("{" + rest)
            except json.JSONDecodeError:
                continue
            out.append((term.strip(), int(data["useCount"]), int(data["lastUsed"]),
                        float(data["frequency"]), float(data["confidence"])))
    return out


def reconcile(store: MemoryStore, concepts_lines: list[str], now: float | None = None) -> int:
    """Snapshot reconciliation: refresh truth + usage for facts still live in ONA's cache."""
    now = time.time() if now is None else now
    rows = parse_concepts(concepts_lines)
    for term, use_count, _last_used, freq, conf in rows:
        if store.get(term) is not None:
            store.upsert(term, freq, conf, now=now)        # keep truth current
            store.touch_usage(term, use_count, now)         # usage signal + wall-clock recency
    return len(rows)


def reload_into_brain(store: MemoryStore, brain: object, limit: int = 40) -> int:
    """Cache-miss repopulation: load pinned + most-relevant facts from L2 back into L1."""
    facts = store.facts_for_reload(limit)
    for fact in facts:
        brain.add_belief(to_statement(fact.narsese, fact.frequency, fact.confidence))  # type: ignore[attr-defined]
    return len(facts)
