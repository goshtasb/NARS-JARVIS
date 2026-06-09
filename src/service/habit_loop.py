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

from habits import (
    app_slug,
    context_key,
    day_type,
    describe_habit,
    eligible,
    evidence_count,
    habit_evidence,
    habit_key,
    habit_term,
    time_bucket,
)

from .autonomy import CONF_FLOOR, gate_passes


def _default_clock() -> datetime:
    return datetime.now().astimezone()


class HabitLoop:
    def __init__(self, brain, store, consent, actuate: Callable[[str, str], object],
                 clock: Callable[[], datetime] = _default_clock,
                 foreground: Callable[[], str] = lambda: "") -> None:
        self._brain = brain
        self._store = store
        self._consent = consent
        self._actuate = actuate            # (action, arg) -> runs the action for real (on approval)
        self._clock = clock
        self._foreground = foreground      # () -> current focused app name ('' if unknown) (ADR-028)
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
        """Record an action as evidence at TWO independent grains (ADR-028) so neither starves: the base
        temporal term (tendency) always, and the full-context term (habit) when the foreground app is
        known. outcome: 'did'/'approved' -> YES, 'denied' -> NO. Non-eligible actions are ignored."""
        if not eligible(action):
            return
        now = self._clock()
        bucket, dt, app = time_bucket(now), day_type(now), app_slug(self._foreground())
        approved = outcome != "denied"
        self._feed(habit_key(bucket, action, arg), approved, bucket, action, arg, "", "", "base")
        if app:   # contextual habit only when we actually know the foreground app
            self._feed(context_key(bucket, action, arg, dt, app), approved, bucket, action, arg, dt, app, "context")

    def _feed(self, key: str, approved: bool, bucket: str, action: str, arg: str,
              dt: str, app: str, scope: str) -> None:
        try:
            self._brain.add_belief(habit_evidence(key, approved))
        except Exception:  # noqa: BLE001
            return
        ans = self._brain.ask(habit_term(key) + "?")
        if ans is not None and ans.truth is not None:
            self._store.record(key, bucket, action, arg, ans.truth.frequency, ans.truth.confidence,
                               day_type=dt, app=app, scope=scope)

    # ── introspection & pruning (ADR-027) — math encapsulated; returns finished text the LLM relays ──
    def describe(self) -> str:
        """A human-readable list of tracked habits + state (no raw NARS numbers to the model)."""
        rows = self._store.list_all()
        if not rows:
            return "I'm not tracking any habits yet."
        arms_at = evidence_count(CONF_FLOOR)
        lines = ["Habits I'm tracking:"]
        for r in rows:
            desc = self._describe_row(r)
            label = "habit" if r.get("scope") == "context" else "tendency"
            if gate_passes(r["frequency"], r["confidence"]):
                lines.append(f"• [{label}] {desc} — [Armed] (I may offer this)")
            else:
                lines.append(f"• [{label}] {desc} — [Learning] (seen ~{evidence_count(r['confidence'])}×, "
                             f"arms at ~{arms_at})")
        return "\n".join(lines)

    @staticmethod
    def _describe_row(r: dict) -> str:
        return describe_habit(r["action"], r["arg"], r["bucket"], r.get("day_type", ""), r.get("app", ""))

    def forget(self, query: str) -> str:
        """Stop tracking habit(s) matching `query`: crater the ONA term (absolute negative) AND purge
        the row. Safe + reversible (JARVIS re-learns if the behaviour recurs)."""
        q = (query or "").strip().lower()
        if not q:
            return "Which habit should I forget?"
        rows = self._store.list_all()
        matches = [r for r in rows if q == r["key"].lower() or q in r["key"].lower()
                   or q in r["action"].lower() or q in (r["arg"] or "").lower()
                   or q in self._describe_row(r).lower()]
        if not matches:
            return f"No habit matches {query!r}."
        forgotten = []
        for r in matches:
            try:
                self._brain.add_belief(habit_evidence(r["key"], approved=False))  # crater {0.0 0.9}
            except Exception:  # noqa: BLE001
                pass
            self._store.delete(r["key"])
            forgotten.append(self._describe_row(r))
        return "Forgotten: " + "; ".join(forgotten) + "."

    # ── proposal: NARS decides, consent gates ──
    def propose_due(self, now: datetime | None = None) -> None:
        """For the current bucket, propose any armed habit (gate_passes) not yet proposed this occurrence.
        Driven from the daemon tick. Opens an ADR-020 consent; never auto-acts."""
        now = now or self._clock()
        bucket, dt, app = time_bucket(now), day_type(now), app_slug(self._foreground())
        day_bucket = now.strftime("%Y-%m-%d") + bucket
        # Specificity-gated (ADR-028): when the foreground app is known, ONLY context habits matching
        # the current (bucket, day_type, app) are candidates — so a Zoom habit can't fire in Spotify.
        # When the app is unknown, fall back to base temporal habits (ADR-026 behaviour preserved).
        rows = (self._store.for_context(bucket, dt, app) if app
                else [r for r in self._store.for_bucket(bucket) if r.get("scope") == "base"])
        for row in rows:
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
