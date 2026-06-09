"""The headless JARVIS session — all reasoning/wiring, zero terminal I/O.

This is the brain behind the socket: it builds the core (models, gate, grounding, the two brains,
voice, executor) and exposes `dispatch(cmd, arg) -> (ok, body)` returning plain JSON-able data, plus
async work pushed through `on_event`. Both the terminal console and the future SwiftUI app are dumb
clients of this same surface, so reasoning logic can never be polluted by — or duplicated in — a UI.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Callable

from actions import ActionRunner
from brain import Brain
from context import render_habits, render_live_context
from execution import DecisionStats, build_air_gapped_executor, decide
from jarvis import Jarvis
from language import IngestionGate, Translator, Voice, strip_acknowledgment
from memory import MemoryStore, MetricsStore, SqliteGroundingStore
from sentinel import SentinelStore, SurpriseDetector, SystemSentinel
from sentinel.narrate import Narrator

import safespawn

from .ax_dispatch import dispatch_ax, find_control_id
from .consent_service import ConsentService
from .sentinel_loop import SentinelLoop
from .voice import WhisperJob, speak, whisper_available
from .wiring import NoNarrationLLM, make_claim_source, make_embedder

_STRONG = DecisionStats(0.95, 0.97, 30, 12)  # an explicit REPL `act` is a high-confidence request
EventSink = Callable[[str, dict], None]
# ADR-022 navigation recipe: the System Settings → Displays pane (holds the Brightness slider).
_DISPLAYS_DEEPLINK = "x-apple.systempreferences:com.apple.Displays-Settings.extension"
_NAV_TIMEOUT = 8.0   # seconds to wait for the opened surface's controls to arrive


class Session:
    def __init__(self, db_path: str = "jarvis.db", on_event: EventSink | None = None) -> None:
        self._emit = on_event or (lambda kind, body: None)
        self._store = MemoryStore(db_path)
        self._brain = Brain(cycles_per_step=50)
        self._act_buf: list[str] = []
        self._executor = build_air_gapped_executor(sink=lambda t: self._act_buf.append(str(t)))
        llm = make_claim_source()
        embedder = make_embedder()
        gate = IngestionGate(embedder) if embedder is not None else None
        grounding = SqliteGroundingStore(db_path) if embedder is not None else None
        self._metrics = MetricsStore(db_path, session_id=uuid.uuid4().hex[:8])
        # Read-only handle on the sentinel's persisted beliefs, so learned habits inject even when the
        # Flow Sentinel loop is OFF (ADR-012). Separate connection from the loop's write-store.
        self._sentinel_store = SentinelStore(db_path)
        # Unified interactive consent (ADR-020): the single owner of "ask the human, then act" for
        # destructive actions, executor confirmation, and Sentinel training.
        self._consent = ConsentService(self._emit)
        voice = Voice(formatter=llm if hasattr(llm, "generate_text") else None)
        self._jarvis = Jarvis(Translator(llm, embedder=embedder, cache=grounding),
                              self._store, self._brain, executor=self._executor, gate=gate,
                              metrics=self._metrics, voice=voice, assistant=llm,  # LLM-first (ADR-007)
                              embedder=embedder,  # auto-memory semantic echo-guard (ADR-008)
                              context_provider=self._live_context,  # dynamic context (ADR-010)
                              habits_provider=self._learned_habits,  # learned sentinel habits (ADR-012)
                              sentinel_beliefs_provider=self._sentinel_store.beliefs,  # grounding (ADR-013)
                              action_runner=ActionRunner(),  # conversational Mac actions (ADR-019)
                              consent_opener=self._open_action_consent,  # destructive-action consent (ADR-020)
                              ax_provider=self._ax_provider,  # GUI actuation: focused-window DOM (ADR-021)
                              ax_dispatch=self._ax_dispatch_verb,  # GUI actuation: verb -> consent -> actuate
                              nav_dispatch=self._nav_dispatch)  # self-navigating recipes (ADR-022)
        # M2 system sentinel (CPU/mem surprise) feeds the knowledge brain; alerts push as events.
        narrator = Narrator(NoNarrationLLM(), on_alert=lambda t: self._emit("alert", {"text": "⚠  " + t}))
        self._sys_detector = SurpriseDetector(self._brain, threshold=0.5, on_surprise=narrator.narrate)
        self._sys_sentinel = SystemSentinel(sink=self._sys_detector.observe, poll_interval=2.0)
        self._last = "no poll yet"
        # ADR-021 GUI actuation: latest focused-window accessibility snapshot pushed by JARVIS.app.
        self._ax_epoch = 0
        self._ax_ids: set[str] = set()
        self._ax_dom = ""
        self._ax_app = ""
        self._pending_nav: dict | None = None   # ADR-022: a navigation recipe awaiting the opened surface
        # ADR-020: the Sentinel asks for consent through the same machine (ask-mode prompts).
        self._flow = SentinelLoop(db_path, self._emit, consent_request=self._consent.request)
        self._pending_learn: dict[str, dict] = {}
        self._voice_jobs: dict[int, WhisperJob] = {}   # fd -> in-flight transcription
        self._token = 0
        self._shutdown = False                          # set by the `shutdown` command (kill switch)

    # ── command plane ─────────────────────────────────────────────────
    def dispatch(self, cmd: str, arg: object = "") -> tuple[bool, object]:
        handler = {
            "ask": self._ask, "tell": self._tell, "learn": self._learn,
            "learn_resolve": self._learn_resolve, "act": self._act,
            "consent_resolve": self._consent_resolve,  # ADR-020: unified approve/deny
            "status": self._status, "health": self._health, "sentinel": self._sentinel,
            "intervene": self._intervene, "voice": self._voice,  # intervene: Sentinel auto-mode undo
            "forget": self._forget, "restore": self._restore,
            "ax_context": self._ax_context, "ax_result": self._ax_result,  # GUI actuation (ADR-021)
            "shutdown": self._do_shutdown,
        }.get(cmd)
        if handler is None:
            return False, {"text": f"unknown command: {cmd!r}"}
        return handler(arg)

    def _ask(self, arg: object) -> tuple[bool, object]:
        text = str(arg).strip()
        if not text:
            return False, {"text": "usage: ask <english question> or ask <narsese?>"}
        raw = text.lstrip()
        if "-->" in raw or raw.startswith(("<", "(")):
            answer = self._jarvis.ask(text)
            return True, {"text": f"answer: {answer}" if answer is not None else "no answer in memory."}
        return True, {"text": self._jarvis.converse(text)}

    def _tell(self, arg: object) -> tuple[bool, object]:
        narsese = str(arg).strip()
        if not narsese:
            return False, {"text": "usage: tell <narsese statement.>"}
        try:
            committed = self._jarvis.tell(narsese)
        except Exception as exc:  # noqa: BLE001 — malformed Narsese -> report, don't crash
            return False, {"text": f"invalid narsese: {exc}"}
        return True, {"text": "committed to L2+L1 (durable)." if committed
                              else "deferred (contradiction flagged)."}

    def _forget(self, arg: object) -> tuple[bool, object]:
        text = str(arg).strip()
        if not text:
            return False, {"text": "usage: forget <fact>"}
        gone = self._jarvis.forget(text)
        return True, {"text": ("forgot: " + "; ".join(gone)) if gone
                              else "nothing matched to forget."}

    def _restore(self, arg: object) -> tuple[bool, object]:
        text = str(arg).strip()
        if not text:
            return False, {"text": "usage: restore <fact>"}
        ok = self._jarvis.restore(text)
        return True, {"text": "restored." if ok else "no such memory to restore."}

    def _learn(self, arg: object) -> tuple[bool, object]:
        sentence = str(arg).strip()
        if not sentence:
            return False, {"text": "usage: learn <english sentence>"}
        rejects: list[dict] = []
        escalations: list[dict] = []
        def on_rejects(items):
            rejects.extend({"mirror": it.english_mirror, "reason": it.reason, "cosine": it.cosine}
                           for it in items)
        def confirm(item):
            escalations.append({"eid": len(escalations), "mirror": item.english_mirror,
                                "cosine": item.cosine, "statement": item.statement})
            return False  # defer to phase B (client asks the human, then learn_resolve commits)
        committed = self._jarvis.learn(sentence, on_rejects=on_rejects, confirm_escalation=confirm)
        token = ""
        if escalations:
            self._token += 1; token = f"L{self._token}"
            self._pending_learn[token] = {"sentence": sentence,
                                          "by_eid": {e["eid"]: e["statement"] for e in escalations}}
        return True, {"committed": committed, "rejects": rejects,
                      "escalations": escalations, "token": token}

    def _learn_resolve(self, arg: object) -> tuple[bool, object]:
        if not isinstance(arg, dict):
            return False, {"text": "learn_resolve expects {token, accept}"}
        pend = self._pending_learn.pop(arg.get("token", ""), None)
        if pend is None:
            return False, {"text": "unknown or expired escalation token"}
        committed: list[str] = []
        for eid in arg.get("accept", []):
            stmt = pend["by_eid"].get(eid)
            if stmt:
                s = self._jarvis.commit_approved(stmt, pend["sentence"])
                if s:
                    committed.append(s)
        return True, {"committed": committed}

    def _act(self, arg: object) -> tuple[bool, object]:
        parts = str(arg).split()
        if len(parts) != 2:
            return False, {"text": "usage: act <op_name> <arg_name>"}
        try:
            proposal = decide(parts[0], parts[1], _STRONG)
        except Exception as exc:  # noqa: BLE001 — surface the security rejection
            return False, {"text": f"rejected: {exc}"}
        self._act_buf = []
        try:
            self._executor.execute(proposal)            # simulate/preview pass (fills _act_buf)
        except Exception as exc:  # noqa: BLE001
            return False, {"text": f"execution error: {exc}"}
        needs = not (proposal.autonomous and self._executor.is_live_eligible(proposal.operation))
        if not needs:
            return True, {"lines": list(self._act_buf), "needs_confirm": False, "consent_id": None}
        # ADR-020: state-changing action -> open a consent request; it runs only on approval. The
        # validated proposal stays server-side (opaque-id boundary); the client gets just the id.
        cid = self._consent.request(kind="action",
                                    prompt=f"Approve and run: {parts[0]} {parts[1]}?",
                                    label=f"{parts[0]} {parts[1]}",
                                    on_approve=lambda p=proposal: self._execute_approved(p),
                                    expiry_default="deny")
        return True, {"lines": list(self._act_buf), "needs_confirm": True, "consent_id": cid}

    def _execute_approved(self, proposal: object) -> str:
        """Consent continuation for an `act` proposal: run it for real, return the actuator output."""
        self._act_buf = []
        try:
            self._executor.execute_approved(proposal)
        except Exception as exc:  # noqa: BLE001
            return f"execution error: {exc}"
        return "\n".join(self._act_buf) or "done."

    def _open_action_consent(self, label: str, on_approve: Callable[[], object]) -> int:
        """Consent opener injected into Jarvis (ADR-020): gate a destructive [[DO:]] action."""
        return self._consent.request(kind="action", prompt=f"Run: {label}?", label=label,
                                     on_approve=on_approve, expiry_default="deny")

    def _consent_resolve(self, arg: object) -> tuple[bool, object]:
        """Unified approve/deny for any pending consent (ADR-020). The client sends only {id,
        accepted}; the daemon runs the matching server-side continuation exactly once."""
        if not isinstance(arg, dict):
            return False, {"text": "consent_resolve expects {id, accepted}"}
        msg = self._consent.resolve(int(arg.get("id", -1)), bool(arg.get("accepted")))
        return True, {"text": msg}

    def consent_snapshot(self) -> dict:
        """The open-consent set + server clock, unicast to a (re)connecting client so it reconciles
        its cards and recomputes TTLs (ADR-020 no-hung-card guarantee)."""
        return self._consent.snapshot()

    # ── GUI actuation plane (ADR-021): JARVIS.app is the eyes+hands; the daemon never holds refs ──
    def _ax_context(self, arg: object) -> tuple[bool, object]:
        """The app pushes the focused window's pruned accessibility DOM + the valid element ids under
        an epoch. We cache only strings; the id->element map stays in the app."""
        if not isinstance(arg, dict):
            return False, {"text": "ax_context expects {epoch, dom, ids}"}
        self._ax_epoch = int(arg.get("epoch", 0))
        self._ax_dom = str(arg.get("dom", ""))
        self._ax_ids = {str(i) for i in (arg.get("ids") or [])}
        self._ax_app = str(arg.get("app", ""))
        self._fulfill_pending_nav()   # ADR-022: the surface we opened may have just arrived
        return True, {"ok": True}

    def _nav_dispatch(self, name: str, arg: str) -> str:
        """Navigation recipe (ADR-022): open the right surface and actuate, regardless of what's
        focused. `set_brightness <0-100>` is safe + reversible -> no consent gate."""
        if name != "set_brightness":
            return f"I don't know how to do that ({name})."
        try:
            value = float(str(arg).strip().rstrip("%").strip())
        except ValueError:
            return f"I can't read {arg!r} as a brightness level."
        sid = find_control_id(self._ax_dom, "AXSlider", "brightness")
        if sid is not None:                       # already on screen -> act now
            self._emit_actuate(self._ax_epoch, sid, "ax_set_value", {"value": value})
            return f"Setting brightness to {int(value)}%."
        try:                                      # else open Displays ourselves, act when it arrives
            safespawn.run(["open", _DISPLAYS_DEEPLINK], capture_output=True, text=True, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return f"Couldn't open Displays settings: {exc}"
        self._pending_nav = {"value": value, "deadline": time.time() + _NAV_TIMEOUT}
        return f"Opening Displays to set brightness to {int(value)}%…"

    def _fulfill_pending_nav(self) -> None:
        """When the opened surface's controls arrive, complete the pending recipe (ADR-022)."""
        if self._pending_nav is None:
            return
        sid = find_control_id(self._ax_dom, "AXSlider", "brightness")
        if sid is None:
            return                                # not this snapshot; keep waiting (until tick expires it)
        value = self._pending_nav["value"]
        self._emit_actuate(self._ax_epoch, sid, "ax_set_value", {"value": value})
        self._emit("answer", {"text": f"Setting brightness to {int(value)}%."})
        self._pending_nav = None

    def _ax_result(self, arg: object) -> tuple[bool, object]:
        """The app reports an actuation outcome; surface it to the user as an answer event."""
        if isinstance(arg, dict):
            self._emit("answer", {"text": str(arg.get("detail", "done."))})
        return True, {"ok": True}

    def _ax_provider(self) -> str:
        """The focused-window controls block injected into the converse prompt (ADR-021). Empty when
        no app has pushed a snapshot (non-app clients / nothing focused)."""
        if not self._ax_dom:
            return ""
        return ("On-screen controls (focused window — you may act on these):\n"
                f"{self._ax_dom}\n"
                "To act, end your reply with [[DO: ax_press: <id>]] or "
                "[[DO: ax_set_value: <id> <value>]] using an id from the list above.")

    def _ax_dispatch_verb(self, verb: str, arg: str) -> str:
        """Validate a [[DO: ax_*]] verb against the current epoch + closed catalog, then consent-gate
        it; on approval it emits the `actuate` event to the app (ADR-021)."""
        return dispatch_ax(self._consent, self._emit_actuate, self._ax_ids, self._ax_epoch, verb, arg)

    def _emit_actuate(self, epoch: int, element_id: str, verb: str, args: dict) -> None:
        self._emit("actuate", {"epoch": epoch, "id": element_id, "verb": verb, "args": args})

    def _status(self, arg: object) -> tuple[bool, object]:
        # ADR-021 visibility: surface what the app's "eyes" last captured, so a failed actuation is
        # debuggable (no AX read at all => Accessibility not granted or nothing focused).
        if self._ax_ids:
            ax = f" | AX: {len(self._ax_ids)} controls from {self._ax_app or '?'} (epoch {self._ax_epoch})"
        else:
            ax = " | AX: no window captured yet (grant Accessibility to JARVIS + focus a window)"
        return True, {"text": f"last poll: {self._last} | L2 facts: {self._store.count()}{ax}"}

    def _live_context(self) -> str:
        """Fresh live-facts block for each converse turn (ADR-010): real local date/time, the latest
        system snapshot, and the sentinel foreground (omitted when the sentinel is off). The single
        place the wall clock / snapshot / foreground are read for context (imperative shell)."""
        return render_live_context(datetime.now().astimezone(), self._last,
                                   self._flow.current_context())

    def _learned_habits(self) -> str:
        """The 'Learned habits' block (ADR-012): the sentinel's confident persisted authorizations,
        translated to English. Sourced from the durable store, so it applies even with the loop off."""
        return render_habits(self._sentinel_store.beliefs())

    def _health(self, arg: object) -> tuple[bool, object]:
        s = self._metrics.summary()
        out: list[str] = []
        if s["total"] == 0:
            out.append("no ingestion telemetry yet — teach me something with `learn`.")
        else:
            out += _ingestion_health(s)
        out += self._flow.focus_health_lines()
        return True, {"text": "\n".join(out)}

    def _sentinel(self, arg: object) -> tuple[bool, object]:
        return True, {"text": self._flow.cmd(str(arg))}

    def _intervene(self, arg: object) -> tuple[bool, object]:
        if not isinstance(arg, dict):
            return False, {"text": "intervene expects {id, accepted}"}
        return True, {"text": self._flow.resolve_intervention(int(arg.get("id", -1)),
                                                              bool(arg.get("accepted")))}

    def _voice(self, arg: object) -> tuple[bool, object]:
        """Push-to-talk: the client wrote a WAV; transcribe it off-loop, then reason + speak. Only a
        path crosses the socket — the audio is a temp file on the shared filesystem."""
        path = arg.get("path", "") if isinstance(arg, dict) else ""
        if not path:
            return False, {"text": "voice expects {path}"}
        if not whisper_available():
            return False, {"text": "voice unavailable — install whisper.cpp (ui/setup-whisper.sh)."}
        try:
            job = WhisperJob(path)
        except Exception as exc:  # noqa: BLE001
            return False, {"text": f"transcription failed to start: {exc}"}
        self._voice_jobs[job.fileno()] = job          # daemon select() now watches its stdout
        return True, {"status": "transcribing"}

    def _read_voice(self, fd: int) -> None:
        job = self._voice_jobs.get(fd)
        if job is None:
            return
        transcript = job.read()
        if transcript is None:
            return                                     # more stdout to come
        del self._voice_jobs[fd]
        job.cleanup()
        self._route_voice(transcript)

    def _route_voice(self, transcript: str) -> None:
        """Speak what we heard, run it through the normal command pipeline, speak the reply."""
        self._emit("transcript", {"text": transcript})
        if not transcript:
            speak("I didn't catch that."); return
        first = transcript.split(" ", 1)[0].lower()
        if first in ("learn", "tell") and " " in transcript:
            cmd, arg = first, transcript.split(" ", 1)[1]
        else:
            cmd, arg = "ask", transcript            # default: treat the utterance as a question
        ok, body = self.dispatch(cmd, arg)
        spoken = body.get("text") if isinstance(body, dict) else None
        if cmd == "learn" and isinstance(body, dict):
            committed = body.get("committed", [])
            spoken = ("saved " + ", ".join(committed)) if committed else "I couldn't save that."
        spoken = spoken or "done."
        self._emit("answer", {"text": spoken})            # on-screen keeps the visible "(Saved: …)"
        speak(strip_acknowledgment(spoken))               # but never voice the confirmation suffix

    def _do_shutdown(self, arg: object) -> tuple[bool, object]:
        """Emergency stop / kill switch: the daemon loop exits after replying, closing the brains,
        the sentinel, and the actuator. The single off-switch for the whole system."""
        self._shutdown = True
        return True, {"text": "shutting down"}

    def wants_shutdown(self) -> bool:
        return self._shutdown

    # ── select-loop hooks for the daemon (sensor pipe + voice jobs) ────
    def extra_fds(self) -> list[int]:
        fds: list[int] = []
        sfd = self._flow.fileno()
        if sfd is not None:
            fds.append(sfd)
        fds += list(self._voice_jobs)
        return fds

    def handle_fd(self, fd: int) -> None:
        if fd in self._voice_jobs:
            self._read_voice(fd)
        elif fd == self._flow.fileno():
            self._flow.read()

    def tick(self) -> None:
        """Periodic M2 system-sentinel poll (CPU/mem -> surprise -> alert event), plus the consent
        expiry sweep (ADR-020): overdue requests default-resolve and emit `consent_closed`."""
        self._consent.sweep(time.time())
        if self._pending_nav is not None and time.time() > self._pending_nav["deadline"]:
            self._emit("answer", {"text": "I couldn't find the brightness control to set."})
            self._pending_nav = None
        try:
            self._sys_sentinel.run_once()
            import psutil
            self._last = f"cpu={psutil.cpu_percent():.0f}% mem={psutil.virtual_memory().percent:.0f}%"
        except Exception as exc:  # noqa: BLE001 — a flaky poll must never kill the loop
            self._emit("alert", {"text": f"[sentinel error] {exc}"})

    def close(self) -> None:
        self._flow.close()
        self._brain.close()
        self._sentinel_store.close()


def _ingestion_health(s: dict) -> list[str]:
    glob = (s["global_rate"] or 0.0) * 100
    out = [f"ingestion health — {s['total']} attempts ({s['session_total']} this session)"]
    sr, pr = s["session_rate"], s["prior_rate"]
    if sr is None:
        out.append(f"  rejection rate: {glob:.0f}% all-time (nothing learned this session yet)")
    elif pr is None:
        out.append(f"  rejection rate: {sr * 100:.0f}% this session (first session — no history)")
    else:
        srp, prp = sr * 100, pr * 100
        trend = ("trending healthy ↓ (learning the dialect)" if srp < prp - 1 else
                 "friction rising ↑ (scope may be too narrow)" if srp > prp + 1 else "steady →")
        small = "  [n small — not yet significant]" if s["session_total"] < 5 else ""
        out.append(f"  rejection rate: {srp:.0f}% this session vs {prp:.0f}% prior -> {trend}{small}")
    fails = {k: v for k, v in s["taxonomy"].items() if k not in ("COMMIT_CLEAN", "ESCALATE_ACCEPTED")}
    if fails:
        label = {"REJECT_STRUCTURAL": "structural", "REJECT_FUSED": "fused",
                 "REJECT_SEMANTIC": "semantic", "ESCALATE_DECLINED": "esc-declined"}
        out.append("  failures: " + " · ".join(f"{label.get(k, k)} {v}" for k, v in sorted(fails.items())))
    return out
