"""Flow-sentinel orchestration, daemon-side. Owns the second isolated brain, the macOS sensor, the
dual-plane funnel, the 0.85-gated detector, interventions, and the focus/calibration KPI.

Lifted out of the console so the daemon is the single owner of the brains and the actuator; it emits
async work to clients via `on_event` (alerts, intervention prompts) and is driven by the daemon's
select loop through `fileno()` / `read()`. Kept separate from the knowledge command plane (Session)
because it is a distinct capability with its own state — splitting it keeps both cohesive (S-01).
"""
from __future__ import annotations

import time
from typing import Callable

from brain import Brain
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

from .autonomy import approved_term, evidence_belief, gate_passes

_DISTRACTION_BUCKETS = frozenset({"comms", "media"})
_BURNIN_FLOOR = 0.85  # ONA confidence the steady baseline must reach before the sentinel may act

EventSink = Callable[[str, dict], None]


class SentinelLoop:
    def __init__(self, db_path: str, on_event: EventSink) -> None:
        self._db_path = db_path
        self._emit = on_event
        self._sensor: Sensor | None = None
        self._brain: Brain | None = None
        self._store: SentinelStore | None = None
        self._detector: SurpriseDetector | None = None
        self._ring, self._frag, self._level = RingState(), DiscState(), None
        self._block, self._recent = BlockState(), []
        self._pending: dict | None = None       # {id, bundles} — one in-flight intervention
        self._next_id = 1
        self._started, self._obs, self._burnin = 0.0, 0, False
        self._events, self._last = 0, None

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
            return (f"sentinel: ON — {self._events} switches, last context: {self._last or '-'}, "
                    f"attention: {self._level or 'focused'} (isolated brain)" if self.running()
                    else "sentinel: OFF   (turn on with: sentinel on)")
        if arg == "on":
            return self._start()
        if arg == "off":
            self.close(); return "sentinel: OFF."
        return "usage: sentinel on | off | status"

    def _start(self) -> str:
        if self.running():
            return "sentinel already on."
        sensor = Sensor()
        if not sensor.start():
            return "sentinel unavailable (needs macOS + swiftc to build the helper)."
        self._sensor = sensor
        self._brain = Brain(cycles_per_step=20)            # SECOND isolated ONA (zero contamination)
        self._store = SentinelStore(self._db_path)
        self._detector = SurpriseDetector(self._brain, threshold=0.5,
                                          on_surprise=self._on_surprise, min_confidence=_BURNIN_FLOOR)
        self._ring, self._frag, self._level = RingState(), DiscState(), None
        self._block, self._recent, self._pending = BlockState(), [], None
        self._started, self._obs, self._burnin = time.monotonic(), 0, False
        self._events, self._last = 0, None
        return ("sentinel: ON — watching app-focus in a fully isolated brain "
                "(silent during burn-in; intervenes only on a confident spike).")

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
                self._detector.observe(steadiness_belief(emitted))
                self._obs += 1
                self._record_burnin(now_wall, now_mono)
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
            except Exception:  # noqa: BLE001
                pass

    def _on_surprise(self, ev) -> None:
        if ev.actual_expectation >= 0.5 or self._pending is not None:
            return
        bundles = list(dict.fromkeys(b for b, _ in self._recent))
        cats = sorted({k for _, k in self._recent})
        iid, self._next_id = self._next_id, self._next_id + 1
        if self._autonomous_ok(cats):
            # Earned autonomy: act now, transparently, with an undo path that revokes trust.
            if self._sensor is not None:
                for bundle in bundles:
                    self._sensor.hide(bundle)
            if self._store is not None:
                self._store.record_intervention(time.time(), True)
            self._pending = {"id": iid, "bundles": bundles, "cats": cats, "ts": time.time(), "mode": "auto"}
            self._emit("acted", {"id": iid, "text": f"Hid {', '.join(cats)} apps — you were fragmenting. (Undo?)"})
        else:
            self._pending = {"id": iid, "bundles": bundles, "cats": cats, "ts": time.time(), "mode": "ask"}
            self._emit("intervention", {"id": iid, "prompt": intervention_prompt(self._level or "fragmented", cats)})

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
