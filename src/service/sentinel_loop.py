"""Flow-sentinel orchestration, daemon-side. Owns the second isolated brain, the macOS sensor, the
dual-plane funnel, the 0.85-gated detector, interventions, and the focus/calibration KPI.

Lifted out of the console so the daemon is the single owner of the brains and the actuator; it emits
async work to clients via `on_event` (alerts, intervention prompts) and is driven by the daemon's
select loop through `fileno()` / `read()`. Kept separate from the knowledge command plane (Session)
because it is a distinct capability with its own state — splitting it keeps both cohesive (S-01).
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable

from brain import Brain
from memory import statement_term
from sentinel import (
    FRAGMENTATION_LADDER,
    BlockState,
    RingState,
    Sensor,
    SentinelStore,
    SurpriseDetector,
    block_update,
    intervention_prompt,
    is_steady,
    rate,
    record,
    steadiness_belief,
)
from sentinel.focusblock import close as block_close
from sentinel.schmitt import DiscState, step

from .autonomy import approved_term, evidence_belief, expectation, gate_passes
from .sentinel_trace import format_gate_proximity, format_observation

_DISTRACTION_BUCKETS = frozenset({"comms", "media"})
_BURNIN_FLOOR = 0.85  # ONA confidence the steady baseline must reach before the sentinel may act


# ── ADR-011: belief persistence (mirrors memory.reload_into_brain). Module functions, not methods,
# so they unit-test with a real Brain + SentinelStore WITHOUT the macOS sensor. ──
def persist_belief(store: SentinelStore, brain: object, term: str, now: float | None = None) -> None:
    """Write-through the current ONA truth for `term` (gate authorization or baseline) to L2."""
    ans = brain.ask(term + "?")  # type: ignore[attr-defined]
    if ans is not None and ans.truth is not None:
        store.record_belief(term, ans.truth.frequency, ans.truth.confidence, now)


def replay_beliefs(store: SentinelStore, brain: object) -> int:
    """Inject every persisted belief truth into a fresh sentinel brain on start; returns the count."""
    persisted = store.beliefs()
    for term, frequency, confidence in persisted:
        brain.add_belief(f"{term}. {{{frequency:.4f} {confidence:.4f}}}")  # type: ignore[attr-defined]
    return len(persisted)

EventSink = Callable[[str, dict], None]


class SentinelLoop:
    def __init__(self, db_path: str, on_event: EventSink,
                 consent_request: Callable | None = None) -> None:
        self._db_path = db_path
        self._emit = on_event
        # ADR-020: ask-mode prompts go through the unified consent machine when wired (the daemon
        # injects ConsentService.request). None => legacy `intervention` event path (tests/offline).
        self._consent_request = consent_request
        self._sensor: Sensor | None = None
        self._brain: Brain | None = None
        self._store: SentinelStore | None = None
        self._detector: SurpriseDetector | None = None
        self._ring, self._frag, self._level = RingState(), DiscState(), None
        self._block, self._recent = BlockState(), []
        self._pending: dict | None = None       # {id, bundles} — one in-flight AUTO-mode undo
        self._ask_open: int | None = None       # consent id of an in-flight ASK-mode prompt (ADR-020)
        self._next_id = 1
        self._started, self._obs, self._burnin = 0.0, 0, False
        self._events, self._last = 0, None
        self._trace, self._dry_run = False, False   # ADR-016: set from env at _start()

    # ── lifecycle / commands ──────────────────────────────────────────
    def running(self) -> bool:
        return self._sensor is not None and self._sensor.running()

    def current_context(self) -> tuple[str | None, str | None]:
        """Foreground (category, attention) for the dynamic-context provider (ADR-010). (None, None)
        when the sentinel is off or has no reading yet — so JARVIS never invents a foreground."""
        if not self.running() or self._last is None:
            return (None, None)
        return (self._last, self._level or "focused")

    def cmd(self, arg: str) -> str:
        arg = (arg or "").strip().lower()
        if arg in ("", "status"):
            if not self.running():
                return "sentinel: OFF   (turn on with: sentinel on)"
            mode = " [DRY-RUN]" if self._dry_run else ""
            return (f"sentinel: ON{mode} — {self._events} switches, last context: {self._last or '-'}, "
                    f"attention: {self._level or 'focused'} (isolated brain)\n  "
                    + self._gate_proximity())
        if arg == "on":
            return self._start()
        if arg == "off":
            self.close(); return "sentinel: OFF."
        return "usage: sentinel on | off | status"

    def _start(self) -> str:
        if self.running():
            return "sentinel already on."
        # ADR-016 observability flags (set before `sentinel on`; default off -> normal behavior).
        self._trace = os.environ.get("NARS_JARVIS_TRACE") not in (None, "", "0")
        self._dry_run = os.environ.get("NARS_JARVIS_DRY_RUN") not in (None, "", "0")
        sensor = Sensor(dry_run=self._dry_run)
        if not sensor.start():
            return "sentinel unavailable (needs macOS + swiftc to build the helper)."
        self._sensor = sensor
        self._store = SentinelStore(self._db_path)         # store first — we replay from it next
        self._brain = Brain(cycles_per_step=20)            # SECOND isolated ONA (zero contamination)
        restored = replay_beliefs(self._store, self._brain)  # ADR-011: restore gate + baseline
        self._detector = SurpriseDetector(self._brain, threshold=0.5,
                                          on_surprise=self._on_surprise, min_confidence=_BURNIN_FLOOR)
        self._ring, self._frag, self._level = RingState(), DiscState(), None
        self._block, self._recent, self._pending = BlockState(), [], None
        self._ask_open = None
        self._started, self._obs = time.monotonic(), 0
        # If the baseline already crossed the floor in a prior session, we're armed immediately.
        self._burnin = self._store.calib()["burnin_observations"] is not None
        self._events, self._last = 0, None
        suffix = f" — restored {restored} learned belief(s), already armed." if restored and self._burnin \
            else f" — restored {restored} learned belief(s)." if restored else ""
        if self._dry_run:
            suffix += " — DRY-RUN (no real hides)"
        if self._trace:
            suffix += " — TRACE on (see daemon log)"
        return ("sentinel: ON — watching app-focus in a fully isolated brain "
                "(silent during burn-in; intervenes only on a confident spike)." + suffix)

    # ── select-loop hooks (daemon multiplexes these) ──────────────────
    def fileno(self) -> int | None:
        return self._sensor.fileno() if self.running() else None

    def read(self) -> None:
        line = self._sensor.readline() if self._sensor else ""
        if not line:
            self.close()
            self._emit("alert", {"text": "[sentinel] sensor stopped."})
            return
        self._handle(line.strip())

    def _handle(self, line: str) -> None:
        kind, _, rest = line.partition(" ")
        if kind != "activate" or not rest:
            return
        bundle, _, ls_cat = rest.partition(" ")
        bucket = self._store.resolve(bundle, ls_cat) if self._store else "?"
        self._last, self._events = bucket, self._events + 1
        if self._store is not None:                    # ADR-050 slice: log the switch for "What I've noticed"
            self._store.record_usage(bundle, bucket, time.time())
        if bucket in _DISTRACTION_BUCKETS:
            self._recent = ([(b, k) for (b, k) in self._recent if b != bundle] + [(bundle, bucket)])[-12:]
        now_mono, now_wall = time.monotonic(), time.time()
        self._ring = record(self._ring, now_mono)
        self._frag, emitted = step(FRAGMENTATION_LADDER, self._frag, rate(self._ring, now_mono))
        if emitted is None:
            return
        self._level = emitted
        self._block, done = block_update(self._block, now_wall, is_steady(emitted))
        if done is not None and self._store is not None:
            self._store.record_focus_block(done.start, done.duration)
        if self._detector is not None:
            try:
                belief = steadiness_belief(emitted)
                self._detector.observe(belief)
                self._obs += 1
                persist_belief(self._store, self._brain, statement_term(belief), now_wall)  # ADR-011
                self._record_burnin(now_wall, now_mono)
                if self._trace:  # ADR-016: surface the per-observation math (numeric/category only)
                    d = self._detector
                    print(format_observation(self._last or "?", emitted, d.last_surprise,
                                             d.last_prior_expectation, d.last_actual_expectation,
                                             d.last_prior_confidence, self._burnin), file=sys.stderr)
            except Exception:  # noqa: BLE001 — a sensor hiccup must not break the loop
                pass

    def _record_burnin(self, now_wall: float, now_mono: float) -> None:
        if (not self._burnin and self._detector is not None
                and self._detector.last_prior_confidence >= _BURNIN_FLOOR):
            elapsed = now_mono - self._started
            if self._store is not None:
                self._store.record_burnin(now_wall, elapsed, self._obs)
            self._burnin = True
            self._emit("alert", {"text": f"[sentinel] baseline reached the {_BURNIN_FLOOR:.2f} floor "
                                         f"after {self._obs} observations ({elapsed / 60:.1f}m) — now armed."})

    def _gate_proximity(self) -> str:
        """ADR-016: per distraction-category, the live gate expectation vs the arm floor."""
        if self._brain is None:
            return format_gate_proximity([])
        items: list[tuple[str, float]] = []
        for cat in sorted(_DISTRACTION_BUCKETS):
            ans = self._brain.ask(approved_term(cat) + "?")
            if ans is not None and ans.truth is not None:
                items.append((cat, expectation(ans.truth.frequency, ans.truth.confidence)))
        return format_gate_proximity(items)

    # ── NARS-gated autonomy: query / feed the procedural appropriateness beliefs ──
    def _autonomous_ok(self, cats: list[str]) -> bool:
        """True iff EVERY involved category has earned autonomy (conf≥0.85 AND favorable)."""
        if not cats or self._brain is None:
            return False
        for cat in cats:
            ans = self._brain.ask(approved_term(cat) + "?")
            if ans is None or ans.truth is None:
                return False
            if not gate_passes(ans.truth.frequency, ans.truth.confidence):
                return False
        return True

    def _feed_consent(self, cats: list[str], approved: bool) -> None:
        """Feed the human's decision back as NAL evidence (asymmetric weights) — the learning loop."""
        if self._brain is None:
            return
        for cat in cats:
            try:
                self._brain.add_belief(evidence_belief(cat, approved))
                persist_belief(self._store, self._brain, approved_term(cat))  # ADR-011: durable gate
            except Exception:  # noqa: BLE001
                pass

    def _on_surprise(self, ev) -> None:
        if ev.actual_expectation >= 0.5 or self._pending is not None or self._ask_open is not None:
            return
        bundles = list(dict.fromkeys(b for b, _ in self._recent))
        cats = sorted({k for _, k in self._recent})
        iid, self._next_id = self._next_id, self._next_id + 1
        if self._autonomous_ok(cats):
            # Earned autonomy: act now, transparently, with an undo path that revokes trust.
            # ADR-016 dry-run: keep the full state machine (pending/undo/KPI) but DON'T actuate;
            # the Sensor is also a hard backstop, so this is belt-and-suspenders.
            if self._sensor is not None and not self._dry_run:
                for bundle in bundles:
                    self._sensor.hide(bundle)
            if self._store is not None:
                self._store.record_intervention(time.time(), True)
            self._pending = {"id": iid, "bundles": bundles, "cats": cats, "ts": time.time(), "mode": "auto"}
            text = (f"[dry-run] WOULD hide {', '.join(cats)} apps (gate passed) — no real hide. (Undo?)"
                    if self._dry_run
                    else f"Hid {', '.join(cats)} apps — you were fragmenting. (Undo?)")
            self._emit("acted", {"id": iid, "text": text})
        elif self._consent_request is not None:
            # ADR-020: ask the human through the unified consent machine. The continuation hides +
            # trains the gate on approval, and trains negative on deny/expiry (default-deny).
            self._open_ask_consent(cats, bundles)
        else:
            # Legacy path (no consent wired — tests/offline): the bespoke intervention event.
            self._pending = {"id": iid, "bundles": bundles, "cats": cats, "ts": time.time(), "mode": "ask"}
            self._emit("intervention", {"id": iid, "prompt": intervention_prompt(self._level or "fragmented", cats)})

    def _open_ask_consent(self, cats: list[str], bundles: list[str]) -> None:
        """Open an ASK-mode consent request (ADR-020). Approval hides the apps and feeds positive
        evidence (training the autonomy gate); deny/expiry feeds heavy negative evidence."""
        ts = time.time()

        def _approve() -> str:
            if self._sensor is not None:
                for bundle in bundles:
                    self._sensor.hide(bundle)
            self._feed_consent(cats, approved=True)
            if self._store is not None:
                self._store.record_intervention(ts, True)
            self._ask_open = None
            return f"✓ hidden: {', '.join(bundles) or '(none running)'}"

        def _deny() -> str:
            self._feed_consent(cats, approved=False)
            if self._store is not None:
                self._store.record_intervention(ts, False)
            self._ask_open = None
            return "ok — I won't hide those."

        self._ask_open = self._consent_request(
            kind="intervention",
            prompt=intervention_prompt(self._level or "fragmented", cats),
            label="hide " + ", ".join(cats) + " apps",
            on_approve=_approve, on_negative=_deny, expiry_default="deny")

    def resolve_intervention(self, iid: int, accepted: bool) -> str:
        if self._pending is None or self._pending["id"] != iid:
            return "no pending intervention."
        pend, self._pending = self._pending, None
        cats, bundles = pend["cats"], pend["bundles"]
        if pend["mode"] == "auto":
            # The action already happened; here `accepted` means "keep it", not-accepted means "undo".
            if accepted:
                return "ok, kept."
            if self._sensor is not None:
                for bundle in bundles:
                    self._sensor.unhide(bundle)
            self._feed_consent(cats, approved=False)         # heavy negative -> revoke autonomy
            if self._store is not None:
                self._store.record_intervention(time.time(), False)
            return "↩ undone — I won't do that automatically anymore."
        # ask mode: this is the explicit consent that trains the gate.
        if accepted and self._sensor is not None:
            for bundle in bundles:
                self._sensor.hide(bundle)
        self._feed_consent(cats, approved=accepted)          # YES -> +evidence, NO -> heavy -evidence
        if self._store is not None:
            self._store.record_intervention(pend["ts"], accepted)
        return (f"✓ hidden: {', '.join(bundles) or '(none running)'}" if accepted
                else "ok — staying out of your way.")

    # ── KPI for the health readout ────────────────────────────────────
    def focus_health_lines(self) -> list[str]:
        store = self._store or SentinelStore(self._db_path)
        try:
            k, c = store.kpi(), store.calib()
        finally:
            if store is not self._store:
                store.close()
        out: list[str] = []
        if c["burnin_observations"] is not None:
            out.append(f"focus sentinel — burn-in: floor reached after "
                       f"{c['burnin_observations']} obs ({(c['burnin_elapsed_s'] or 0) / 60:.1f}m).")
        else:
            out.append("focus sentinel — burn-in: floor not yet reached (still building baseline).")
        if c["fired"]:
            out.append(f"  interventions: {c['fired']} fired, {c['declined']} declined "
                       f"(decline rate {(c['decline_rate'] or 0.0) * 100:.0f}% — false-positive proxy).")
        if k["accepted"] and k["pre_median_s"] is not None and k["post_median_s"] is not None:
            pre, post = k["pre_median_s"] / 60, k["post_median_s"] / 60
            delta = (k["delta_s"] or 0.0) / 60
            out.append(f"  lift: median focus block {pre:.0f}m → {post:.0f}m after "
                       f"({'+' if delta >= 0 else ''}{delta:.0f}m protected).")
        return out

    def close(self) -> None:
        if self._store is not None:
            done = block_close(self._block, time.time())
            if done is not None:
                self._store.record_focus_block(done.start, done.duration)
        if self._sensor:
            self._sensor.stop(); self._sensor = None
        if self._brain:
            self._brain.close(); self._brain = None
        if self._store:
            self._store.close(); self._store = None
        self._block, self._pending, self._detector = BlockState(), None, None
