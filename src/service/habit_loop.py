"""The Habit Brain loop (ADR-026 Phase 1) — Imperative Shell (S-02).

Wires the execution layer to NARS: each eligible action you take/approve becomes asymmetric evidence on
a quantized `(hour, action[, arg])` term in the knowledge brain. When that term's gate crosses 0.85
(~6 confirmations), the proposal tick offers the action through the ADR-020 consent gate. It only ever
*proposes* — the human approve is the trigger (and reinforces the habit); a deny collapses it fast.

Reuses the Sentinel's verified gate (`service.autonomy.gate_passes`) and the ADR-011 replay pattern.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from habits import eligible, habit_evidence, habit_key, habit_term, time_bucket

from .autonomy import gate_passes


def _default_clock() -> datetime:
    return datetime.now().astimezone()


class HabitLoop:
    def __init__(self, brain, store, consent, actuate: Callable[[str, str], object],
                 clock: Callable[[], datetime] = _default_clock) -> None:
        self._brain = brain
        self._store = store
        self._consent = consent
        self._actuate = actuate            # (action, arg) -> runs the action for real (on approval)
        self._clock = clock
        self._replay()

    def _replay(self) -> None:
        """ADR-011: re-inject persisted habit truths into a fresh ONA on start (ONA has no save)."""
        for key, freq, conf in self._store.all():
            try:
                self._brain.add_belief(f"{habit_term(key)}. {{{freq:.4f} {conf:.4f}}}")
            except Exception:  # noqa: BLE001
                pass

    # ── telemetry: execution -> NARS evidence ──
    def observe(self, action: str, arg: str = "", outcome: str = "did") -> None:
        """Record an action as evidence. outcome: 'did'/'approved' -> YES, 'denied' -> NO. Non-eligible
        actions (read-only / destructive) are ignored, so they never become habits."""
        if not eligible(action):
            return
        bucket = time_bucket(self._clock())
        key = habit_key(bucket, action, arg)
        try:
            self._brain.add_belief(habit_evidence(key, approved=outcome != "denied"))
        except Exception:  # noqa: BLE001
            return
        self._persist(key, bucket, action, arg)

    def _persist(self, key: str, bucket: str, action: str, arg: str) -> None:
        ans = self._brain.ask(habit_term(key) + "?")
        if ans is not None and ans.truth is not None:
            self._store.record(key, bucket, action, arg, ans.truth.frequency, ans.truth.confidence)

    # ── proposal: NARS decides, consent gates ──
    def propose_due(self, now: datetime | None = None) -> None:
        """For the current bucket, propose any armed habit (gate_passes) not yet proposed this occurrence.
        Driven from the daemon tick. Opens an ADR-020 consent; never auto-acts."""
        now = now or self._clock()
        bucket = time_bucket(now)
        day_bucket = now.strftime("%Y-%m-%d") + bucket
        for row in self._store.for_bucket(bucket):
            if row["last_proposed"] == day_bucket:
                continue                                  # cooldown: once per occurrence
            ans = self._brain.ask(habit_term(row["key"]) + "?")
            if ans is None or ans.truth is None:
                continue
            if not gate_passes(ans.truth.frequency, ans.truth.confidence):
                continue
            self._store.mark_proposed(row["key"], day_bucket)
            self._open_proposal(row["action"], row["arg"])

    def _open_proposal(self, action: str, arg: str) -> None:
        label = f"{action}{(' ' + arg) if arg else ''}"

        def approve() -> str:
            result = self._actuate(action, arg)
            self.observe(action, arg, "approved")          # approval reinforces the habit
            return result if isinstance(result, str) else f"done — {label}"

        def deny():
            self.observe(action, arg, "denied")            # one deny collapses it fast

        self._consent.request(kind="habit",
                              prompt=f"You usually {label} around this time — want me to now?",
                              label=label, on_approve=approve, on_negative=deny, expiry_default="deny")
