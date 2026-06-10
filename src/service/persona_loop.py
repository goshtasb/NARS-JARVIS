"""The persona ingestion loop (ADR-036) — Imperative Shell (S-02).

Drives the continuous persona layer from the daemon tick, but ONLY when the daemon is idle (or an
overnight run is active), so the blocking 7B extraction never steals compute from a live turn. Each
drain pops a bounded batch, runs ONE 7B extraction → validated in-vocabulary Narsese, feeds the
ISOLATED persona ONA, and write-throughs the resulting `(term, freq, conf)` to SQLite (the ADR-011
pattern). Injection reads SQLite directly (elsewhere), never this loop, so it's off the hot path.

Fail-closed: if the persona ONA dies beyond recovery (`BrainUnavailable`), the layer goes DOWN —
ingestion stops and injection is disabled (stateless), logged once. The conversational brain and the
deterministic action firewall are untouched regardless.
"""
from __future__ import annotations

from typing import Callable

from brain import BrainUnavailable
from persona import extract

IDLE_SECONDS = 45.0   # ADR-036 locked thresholds
BATCH_MAX = 5
INJECT_FLOOR = 0.75
PRUNE_FLOOR = 0.10


class PersonaLoop:
    def __init__(self, brain, store, generate: Callable[[str, str, int], str],
                 emit: Callable[[str, dict], None] = lambda k, b: None) -> None:
        self._brain = brain          # an ISOLATED, resilient persona ONA (on_restart -> self.replay)
        self._store = store
        self._generate = generate    # (system, user, max_tokens) -> str — the daemon's LLM
        self._emit = emit
        self._down = False           # True after an unrecoverable NAR crash -> fail closed
        self.replay()                # boot: re-feed the checkpoint into the fresh persona brain

    def replay(self) -> None:
        """Re-feed every checkpointed concept into the persona ONA. Boot + the Brain.on_restart hook."""
        for c in self._store.all_concepts():
            try:
                self._brain.add_belief(f"{c['term']}. {{{c['frequency']:.4f} {c['confidence']:.4f}}}")
            except Exception:  # noqa: BLE001 — a single bad row must not abort replay
                pass

    # ── producer side (called from the action/web paths; just an O(1) buffer append) ──
    def observe(self, text: str, kind: str = "event") -> None:
        if not self._down:
            self._store.buffer_event(text, kind)

    # ── consumer side (driven from session.tick, idle-gated) ──
    def tick(self, idle: bool, overnight_active: bool = False) -> None:
        if self._down or not (idle or overnight_active):
            return
        batch = self._store.pending_batch(BATCH_MAX)
        if not batch:
            return
        try:
            for term, freq, conf in extract([b["raw_text"] for b in batch], self._generate):
                self._brain.add_belief(f"{term}. {{{freq:.4f} {conf:.4f}}}")
                ans = self._brain.ask(f"{term}?")
                if ans is not None and ans.truth is not None:
                    self._store.upsert_concept(term, ans.truth.frequency, ans.truth.confidence)
            self._store.consume([b["id"] for b in batch])
            self._store.prune(PRUNE_FLOOR)
        except BrainUnavailable as exc:
            self._down = True
            self._emit("alert", {"text": f"[COGNITIVE LAYER ERROR: persona engine down — {exc}]"})

    # ── injection source (read by context.render_persona; empty when down -> stateless) ──
    def persona(self) -> list[dict]:
        return [] if self._down else self._store.current(INJECT_FLOOR)

    @property
    def down(self) -> bool:
        return self._down
