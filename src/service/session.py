"""The headless JARVIS session — all reasoning/wiring, zero terminal I/O.

This is the brain behind the socket: it builds the core (models, gate, grounding, the two brains,
voice, executor) and exposes `dispatch(cmd, arg) -> (ok, body)` returning plain JSON-able data, plus
async work pushed through `on_event`. Both the terminal console and the future SwiftUI app are dumb
clients of this same surface, so reasoning logic can never be polluted by — or duplicated in — a UI.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Callable

from actions import (
    ActionRunner, alternatives, build_intent_grammar, build_system_prompt, recipe_for,
    resolve as resolve_action, schema as catalog_schema, should_gate, validate_intent,
)
from brain import Brain
from habits import HabitStore
from persona import PersonaStore, render_persona
from overnight import HeldLedger, OvernightQueue, safe_autonomous
from context import render_habits, render_live_context
from execution import DecisionStats, build_air_gapped_executor, decide
from jarvis import Jarvis
from language import IngestionGate, Translator, Voice, strip_acknowledgment
from memory import MemoryStore, MetricsStore, SqliteGroundingStore
from sentinel import SentinelStore, SurpriseDetector, SystemSentinel, summarize_usage
from sentinel.narrate import Narrator

import safespawn

from .agent_loop import agent_route, resolve_surface
from .ax_dispatch import dispatch_ax, find_control_id
from .consent_service import ConsentService
from .habit_loop import HabitLoop
from .overnight_runner import OvernightRunner
from .persona_loop import IDLE_SECONDS, PersonaLoop
from .sentinel_loop import SentinelLoop
from .voice import WhisperJob, speak, whisper_available
from .wiring import NoNarrationLLM, make_claim_source, make_embedder

_STRONG = DecisionStats(0.95, 0.97, 30, 12)  # an explicit REPL `act` is a high-confidence request
EventSink = Callable[[str, dict], None]
_NAV_TIMEOUT = 8.0   # ADR-023: seconds to wait for an opened surface's controls to arrive (else give up)
_MAX_HOPS = 2        # ADR-024 P2: max navigations in one agent loop (circuit breaker)
_MAX_STEPS = 3       # ADR-024 P2: max re-prompt steps in one loop (bounds LLM cost)
_AGENT_TTL = 12.0    # ADR-024 P2: seconds an agent loop may run before giving up
# ADR-033: kinds offered in the Batch Canvas palette — overnight-appropriate (excludes ax/agent/habit,
# which need live GUI context or aren't tasks). work/query/diag -> Autonomous; argv/nav -> Held.
_CANVAS_KINDS = ("work", "query", "diag", "argv", "nav")


class Session:
    def __init__(self, db_path: str = "jarvis.db", on_event: EventSink | None = None) -> None:
        self._emit = on_event or (lambda kind, body: None)
        self._store = MemoryStore(db_path)
        self._brain = Brain(cycles_per_step=50)
        self._act_buf: list[str] = []
        self._executor = build_air_gapped_executor(sink=lambda t: self._act_buf.append(str(t)))
        llm = make_claim_source()
        self._llm = llm                  # ADR-054: handle for the GBNF-constrained intent router
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
        # Conversational Mac actions (ADR-019) — one instance, reused by converse + habit actuation.
        # llm is injected for ADR-032 kind="work" actions (summarize_file); read-only actions ignore it.
        self._actions = ActionRunner(llm=llm)
        # Habit Brain (ADR-026): execution -> NARS evidence; armed habits propose via the consent gate.
        self._habit_store = HabitStore(db_path)
        self._habit_loop = HabitLoop(self._brain, self._habit_store, self._consent, self._habit_actuate,
                                     foreground=lambda: self._ax_app)  # ADR-028: app context for habits
        # Overnight batch queue + persistent held-ledger (ADR-031): run read-only tasks unattended,
        # hold everything else for explicit morning approval. Durable across daemon restarts.
        self._overnight_queue = OvernightQueue(db_path)
        self._held_ledger = HeldLedger(db_path)
        self._overnight = OvernightRunner(self._overnight_queue, self._held_ledger, self._actions, self._emit)
        # Persona cognitive layer (ADR-036): an ISOLATED, resilient persona ONA learns the user's
        # style/focus from idle-gated batches; the system prompt is injected from SQLite. Separate brain
        # so persona concepts never crowd the conversational memory bag.
        self._persona_store = PersonaStore(db_path)
        self._persona_loop = None  # set just below; referenced by the brain's on_restart hook
        _persona_gen = ((lambda s, u, mt: llm.generate_text(s, u, max_tokens=mt))
                        if hasattr(llm, "generate_text") else (lambda s, u, mt: ""))
        self._persona_brain = Brain(cycles_per_step=50,
                                    on_restart=lambda _b: self._persona_loop and self._persona_loop.replay())
        self._persona_loop = PersonaLoop(self._persona_brain, self._persona_store, _persona_gen, self._emit)
        self._last_request_at = 0.0
        voice = Voice(formatter=llm if hasattr(llm, "generate_text") else None)
        self._jarvis = Jarvis(Translator(llm, embedder=embedder, cache=grounding),
                              self._store, self._brain, executor=self._executor, gate=gate,
                              metrics=self._metrics, voice=voice, assistant=llm,  # LLM-first (ADR-007)
                              embedder=embedder,  # auto-memory semantic echo-guard (ADR-008)
                              context_provider=self._live_context,  # dynamic context (ADR-010)
                              habits_provider=self._learned_habits,  # learned sentinel habits (ADR-012)
                              persona_provider=lambda: render_persona(self._persona_loop.persona()),  # ADR-036

                              sentinel_beliefs_provider=self._sentinel_store.beliefs,  # grounding (ADR-013)
                              action_runner=self._actions,  # conversational Mac actions (ADR-019)
                              habit_observer=self._habit_loop.observe,  # execution -> habit evidence (ADR-026)
                              habit_admin=self._habit_admin,  # list/forget habits (ADR-027)
                              consent_opener=self._open_action_consent,  # destructive-action consent (ADR-020)
                              ax_provider=self._ax_provider,  # GUI actuation: focused-window DOM (ADR-021)
                              ax_dispatch=self._ax_dispatch_verb,  # GUI actuation: verb -> consent -> actuate
                              nav_dispatch=self._nav_dispatch,  # self-navigating recipes (ADR-022)
                              navigate=self._navigate)  # bounded agent loop (ADR-024 P2)
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
        self._agent: dict | None = None          # ADR-024 P2: an active bounded agent loop, or None
        # ADR-020: the Sentinel asks for consent through the same machine (ask-mode prompts).
        self._flow = SentinelLoop(db_path, self._emit, consent_request=self._consent.request)
        self._pending_learn: dict[str, dict] = {}
        self._voice_jobs: dict[int, WhisperJob] = {}   # fd -> in-flight transcription
        self._cloud_jobs: dict[int, dict] = {}         # ADR-056: fd -> in-flight off-loop cloud call
        self._token = 0
        self._shutdown = False                          # set by the `shutdown` command (kill switch)
        # ADR-048: AUTO-START the Flow Sentinel at boot if the user hasn't turned it off — so app-focus
        # learning resumes on every restart without a manual `sentinel on` (the silent-no-learning bug).
        # Failure (no swiftc, etc.) is swallowed: the sensor stays off, the daemon boots normally.
        if self._sentinel_store.enabled():
            try:
                self._flow.cmd("on")
            except Exception:  # noqa: BLE001 — sentinel start must never block daemon boot
                pass

    # ── command plane ─────────────────────────────────────────────────
    def dispatch(self, cmd: str, arg: object = "") -> tuple[bool, object]:
        handler = {
            "ask": self._ask, "tell": self._tell, "learn": self._learn,
            "learn_resolve": self._learn_resolve, "act": self._act,
            "consent_resolve": self._consent_resolve,  # ADR-020: unified approve/deny
            "status": self._status, "health": self._health, "sentinel": self._sentinel,
            "intervene": self._intervene, "voice": self._voice,  # intervene: Sentinel auto-mode undo
            "forget": self._forget, "restore": self._restore,
            "habits": self._habits, "habit_forget": self._habit_forget,  # ADR-030: menu-bar dashboard
            "persona_list": self._persona_list, "persona_forget": self._persona_forget,  # ADR-037: glass box
            "usage": self._usage,  # ADR-050 slice: "What I've noticed about your computer use"
            "chat_clear": self._chat_clear,  # ADR-041: end the short-term conversation window on demand
            "overnight_enqueue": self._overnight_enqueue, "overnight_start": self._overnight_start,
            "overnight_status": self._overnight_status,                   # ADR-031: overnight batch queue
            "overnight_enqueue_batch": self._overnight_enqueue_batch,     # ADR-033: batch commit
            "overnight_schedule_batch": self._overnight_schedule_batch,   # ADR-053: commit with a run_at
            "action_alternatives": self._action_alternatives,             # ADR-053: failed-task recovery routing
            "cloud_ask": self._cloud_ask,                                 # ADR-056: General Mode — off-loop cloud answer
            "egress_log": self._egress_log,                               # ADR-056: Privacy Receipts (Identity tab)
            "intent_parse": self._intent_parse,                           # ADR-054: NL -> validated Canvas intent
            "catalog_schema": self._catalog_schema,                       # ADR-033: palette for the canvas
            "briefing": self._briefing, "briefing_resolve": self._briefing_resolve,  # ADR-031: morning
            "briefing_dismiss_done": self._briefing_dismiss_done,         # ADR-033: clear completed
            "ax_context": self._ax_context, "ax_result": self._ax_result,  # GUI actuation (ADR-021)
            "axdump": self._axdump,  # ADR-023: inspect the captured control tree (recipe-matcher authoring)
            "shutdown": self._do_shutdown,
        }.get(cmd)
        if handler is None:
            return False, {"text": f"unknown command: {cmd!r}"}
        self._last_request_at = time.time()   # ADR-036: gate persona ingestion to idle windows only
        return handler(arg)

    def _ask(self, arg: object) -> tuple[bool, object]:
        text = str(arg).strip()
        if not text:
            return False, {"text": "usage: ask <english question> or ask <narsese?>"}
        raw = text.lstrip()
        if "-->" in raw or raw.startswith(("<", "(")):
            answer = self._jarvis.ask(text)
            return True, {"text": f"answer: {answer}" if answer is not None else "no answer in memory."}
        self._persona_loop.observe(text, "user")   # ADR-036: buffer the utterance for idle batch learning
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
        if self._agent is not None:   # ADR-024 P2: note the fresh DOM; tick() drives the step once settled
            self._agent["pending_epoch"] = self._ax_epoch
        return True, {"ok": True}

    def _nav_dispatch(self, name: str, arg: str) -> str:
        """Generic, table-driven navigation recipe (ADR-023): look up the intent in the declarative
        RECIPES table, open its surface if needed, find its control, and actuate per the row's friction
        policy. No per-domain branch logic — adding a domain is a data row in actions/recipes.py. An
        unknown intent fails safe (the general, always-gated ax_* verbs handle anything off-table)."""
        r = recipe_for(name)
        if r is None:
            return f"I can't do that ({name})."
        value: float | None = None
        if r.takes_value:
            try:
                value = float(str(arg).strip().rstrip("%").strip())
            except ValueError:
                return f"I can't read {arg!r} as a value."
        else:
            value = r.fixed_value     # e.g. an idempotent toggle's desired state (1=on)
        sid = find_control_id(self._ax_dom, r.role, r.title)
        if sid is not None:                       # already on screen -> act now
            return self._actuate_recipe(r, sid, value)
        if r.surface:                             # else open the surface, act when its controls arrive
            try:
                safespawn.run(["open", r.surface], capture_output=True, text=True, timeout=10)
            except Exception as exc:  # noqa: BLE001
                return f"Couldn't open the settings for that: {exc}"
            self._pending_nav = {"recipe": r, "value": value, "deadline": time.time() + _NAV_TIMEOUT}
            return f"Opening settings to {r.label.split(' (')[0]}…"
        return f"I couldn't find the control for {name}."

    def _fulfill_pending_nav(self) -> None:
        """When the opened surface's controls arrive (a fresh ax_context), complete the pending recipe
        by re-matching ITS (role, title) and actuating (ADR-023)."""
        if self._pending_nav is None:
            return
        r, value = self._pending_nav["recipe"], self._pending_nav["value"]
        sid = find_control_id(self._ax_dom, r.role, r.title)
        if sid is None:
            return                                # not this snapshot; keep waiting (tick expires it)
        self._emit("answer", {"text": self._actuate_recipe(r, sid, value)})
        self._pending_nav = None

    def _actuate_recipe(self, r, sid: str, value: float | None) -> str:
        """The single friction decision (ADR-023): `should_gate(r)` — read from the recipe's DATA, never
        the LLM. FRICTIONLESS -> emit the actuate directly; GATED -> route through the consent gate."""
        if should_gate(r):
            ax_arg = sid if r.verb == "ax_press" else f"{sid} {value}"
            return dispatch_ax(self._consent, self._emit_actuate, self._ax_ids, self._ax_epoch, r.verb, ax_arg)
        args = {"value": value} if r.verb in ("ax_set_value", "ax_set_checked") else {}
        self._emit_actuate(self._ax_epoch, sid, r.verb, args)
        return self._recipe_done_msg(r, value)

    @staticmethod
    def _recipe_done_msg(r, value: float | None) -> str:
        if r.takes_value and value is not None:
            noun = r.intent.removeprefix("set_").replace("_", " ")
            return f"Setting {noun} to {int(value)}%."
        return f"Done — {r.intent.replace('_', ' ')}."

    def _habit_actuate(self, action: str, arg: str) -> str:
        """Run an approved habit for real (ADR-026) — route like converse: a recipe via nav, else the
        action runner. Reached only after the human approves the proposal."""
        a = resolve_action(action)
        if a is not None and a.kind == "nav":
            return self._nav_dispatch(action, arg)
        return self._actions.perform(action, arg)

    def _habit_admin(self, verb: str, arg: str) -> str:
        """Habit introspection/pruning (ADR-027): list what's learned or forget a habit."""
        if verb == "forget_habit":
            return self._habit_loop.forget(arg)
        return self._habit_loop.describe()

    def _habits(self, _arg: object) -> tuple[bool, object]:
        """ADR-030: structured habit snapshot for the menu-bar dashboard (no LLM round-trip)."""
        return True, {"rows": self._habit_loop.snapshot()}

    def _habit_forget(self, arg: object) -> tuple[bool, object]:
        """ADR-030: one-click Forget from the dashboard. Routes through HabitLoop.forget so the ONA
        term is cratered (not a raw row delete) — distinct from the memory `forget` command."""
        return True, {"text": self._habit_loop.forget(str(arg).strip())}

    # ── ADR-037: persona introspection & control (the Cognitive Identity glass box) ──
    def _persona_list(self, _arg: object) -> tuple[bool, object]:
        return True, {"rows": self._persona_loop.snapshot()}

    def _usage(self, arg: object) -> tuple[bool, object]:
        """ADR-050 (passive-observation slice): the 'What I've noticed about your computer use' summary,
        aggregated from the content-blind app-switch log. `arg` = optional lookback in days (default 7)."""
        try:
            days = float(str(arg).strip()) if str(arg).strip() else 7.0
        except ValueError:
            days = 7.0
        now = time.time()
        text = summarize_usage(self._sentinel_store.recent_usage(now - days * 86400), now)
        return True, {"text": text or "I haven't observed enough activity yet — leave me running and "
                                      "the Sentinel will build a picture of how you use your Mac."}

    def _chat_clear(self, _arg: object) -> tuple[bool, object]:
        """ADR-041: explicit session boundary — drop the sliding conversation window (short-term only;
        durable memory / habits / persona are untouched)."""
        self._jarvis.clear_conversation()
        return True, {"text": "(Conversation context cleared.)"}

    def _persona_forget(self, arg: object) -> tuple[bool, object]:
        """Delete a learned persona constraint + crater its belief in the isolated persona ONA."""
        return True, {"text": self._persona_loop.forget(str(arg).strip())}

    # ── ADR-031: overnight batch queue + morning briefing ──
    def _overnight_enqueue(self, arg: object) -> tuple[bool, object]:
        """Queue one concrete catalog action for the overnight run. arg: {action, arg} or
        'action arg…' (the console form)."""
        if isinstance(arg, dict):
            action, a = str(arg.get("action", "")).strip(), str(arg.get("arg", "")).strip()
        else:
            parts = str(arg).strip().split(" ", 1)
            action, a = parts[0], (parts[1] if len(parts) > 1 else "")
        if not action:
            return False, {"text": "usage: overnight_enqueue <action> [arg]"}
        if resolve_action(action) is None:
            return False, {"text": f"unknown action: {action!r} (only catalog actions can be queued)."}
        tid = self._overnight_queue.enqueue(action, a)
        return True, {"text": f"queued #{tid}: {action}{(' ' + a) if a else ''}", "id": tid}

    def _overnight_start(self, _arg: object) -> tuple[bool, object]:
        """Begin draining the queue (call at bedtime). Read-only tasks run; the rest are held."""
        n = self._overnight.start()
        return True, {"text": f"overnight run started — {n} task(s) queued.", "queued": n}

    def _overnight_status(self, _arg: object) -> tuple[bool, object]:
        return True, {"active": self._overnight.active, "rows": self._overnight_queue.list_all()}

    def _briefing(self, _arg: object) -> tuple[bool, object]:
        """The morning report: completed tasks + the actions held for approval."""
        rows = self._overnight_queue.list_all()
        done = [r for r in rows if r["status"] in ("done", "failed")]
        return True, {"done": done, "held": self._held_ledger.pending()}

    def _briefing_resolve(self, arg: object) -> tuple[bool, object]:
        """Approve/deny a held action. On approve, run it NOW — the briefing click IS the consent gate."""
        if not isinstance(arg, dict):
            return False, {"text": "usage: briefing_resolve {id, accepted}"}
        hid, accepted = int(arg.get("id", 0)), bool(arg.get("accepted"))
        row = self._held_ledger.get(hid)
        if row is None or row["disposition"] != "held":
            return True, {"text": "no held action with that id (already resolved?)."}
        self._held_ledger.resolve(hid, accepted)
        if not accepted:
            return True, {"text": f"declined: {row['action']}"}
        result = self._actions.perform(row["action"], row["arg"])   # human just approved -> execute
        return True, {"text": result}

    # ── ADR-033: Batch Canvas (palette schema, batch commit, clear-completed) ──
    def _catalog_schema(self, _arg: object) -> tuple[bool, object]:
        """The Batch Canvas palette: overnight-appropriate actions annotated with their autonomous/held
        tag. The autonomy call lives here (session imports both actions + overnight), keeping the catalog
        ignorant of overnight semantics and the Swift UI free of business logic."""
        actions = [{**a, "autonomous": safe_autonomous(resolve_action(a["name"]))}
                   for a in catalog_schema() if a["kind"] in _CANVAS_KINDS]
        return True, {"actions": actions}

    def _enqueue_items(self, items: object, run_at: float | None) -> tuple[list[int], list[str]]:
        """Shared commit: validate each {action, arg} against the catalog and enqueue with `run_at`
        (None = manual/Run Now; an epoch = scheduled). Unknown actions are rejected, never queued."""
        queued, rejected = [], []
        for it in (items if isinstance(items, list) else []):
            if not isinstance(it, dict):
                continue
            name, a = str(it.get("action", "")).strip(), str(it.get("arg", "")).strip()
            if not name or resolve_action(name) is None:
                rejected.append(name or "(empty)")
                continue
            queued.append(self._overnight_queue.enqueue(name, a, run_at=run_at))
        return queued, rejected

    def _overnight_enqueue_batch(self, arg: object) -> tuple[bool, object]:
        """Commit a composed batch to run now (manual): arg = [{action, arg}, …]."""
        queued, rejected = self._enqueue_items(arg, run_at=None)
        return True, {"queued": len(queued), "rejected": rejected,
                      "text": f"committed {len(queued)} task(s)"
                              + (f"; rejected {len(rejected)}" if rejected else "")}

    def _overnight_schedule_batch(self, arg: object) -> tuple[bool, object]:
        """ADR-053: commit a batch to run at a scheduled time. arg = {items: [{action, arg}, …],
        run_at: <absolute epoch>}. The client computes the epoch (Tonight / In N hours), so the daemon
        carries no timezone logic. The runner auto-activates once `run_at <= now` (no manual start)."""
        if not isinstance(arg, dict) or not isinstance(arg.get("run_at"), (int, float)):
            return False, {"text": "usage: {items: [{action, arg}…], run_at: <epoch>}"}
        run_at = float(arg["run_at"])
        queued, rejected = self._enqueue_items(arg.get("items"), run_at=run_at)
        return True, {"queued": len(queued), "rejected": rejected, "run_at": run_at,
                      "text": f"scheduled {len(queued)} task(s)"
                              + (f"; rejected {len(rejected)}" if rejected else "")}

    def _action_alternatives(self, arg: object) -> tuple[bool, object]:
        """ADR-053: sibling tools to offer for a FAILED task. arg = {action, arg}. Returns each
        alternative enriched with its catalog label + autonomous tag, so the Canvas renders the
        'Change tool ▾' menu without business logic (the dumb-client rule, ADR-033)."""
        if not isinstance(arg, dict):
            return False, {"text": "usage: {action, arg}"}
        names = alternatives(str(arg.get("action", "")), str(arg.get("arg", "")))
        out = []
        for n in names:
            act = resolve_action(n)
            if act is not None:
                out.append({"name": n, "label": act.label, "autonomous": safe_autonomous(act)})
        return True, {"alternatives": out}

    def _intent_parse(self, arg: object) -> tuple[bool, object]:
        """ADR-054: turn one NL request into a VALIDATED Canvas intent via the GBNF-constrained 7B.
        arg = {text, action?(a verb pinned by the / override)}. Returns {ok, intent:{action,arg,timing}}
        or {ok:false, clarify}. Runs on the foreground pipeline (interactive); the offload worker is
        untouched. Never commits — the client resolves timing->epoch and projects the row to the Canvas."""
        if not isinstance(arg, dict) or not str(arg.get("text", "")).strip():
            return False, {"text": "usage: {text, action?}"}
        if not hasattr(self._llm, "generate_json"):
            return True, {"ok": False, "clarify": "The local language model isn't loaded, so I can't parse that."}
        text = str(arg["text"]).strip()
        pinned = str(arg.get("action", "")).strip()
        acts = [a for a in catalog_schema() if a["kind"] in _CANVAS_KINDS]
        names = [a["name"] for a in acts]
        valid, arg_req = set(names), {a["name"] for a in acts if a.get("takes_arg")}
        # The / override pins the verb: FORCE the grammar to that action with NO `none` option, so the
        # user's explicit choice can't be overridden — the model only extracts arg + timing. Otherwise
        # it picks the verb from the full catalog and may decline via `none`.
        is_pinned = pinned in valid
        grammar = build_intent_grammar([pinned] if is_pinned else names, include_none=not is_pinned)
        sysp = build_system_prompt([f"{a['name']} — {a['label']}" for a in acts],
                                   time.strftime("%A %Y-%m-%d %H:%M"))
        try:
            raw = self._llm.generate_json(sysp, text, grammar, max_tokens=200)
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 — a parse miss reports, never crashes the turn
            return True, {"ok": False, "clarify": f"I couldn't parse that request ({exc})."}
        ok, result = validate_intent(payload, valid, arg_req)
        return True, ({"ok": True, "intent": result} if ok else {"ok": False, "clarify": result["clarify"]})

    def _briefing_dismiss_done(self, _arg: object) -> tuple[bool, object]:
        """Clear finished (done/failed) rows so the briefing doesn't grow forever. Held/pending untouched."""
        return True, {"cleared": self._overnight_queue.purge_done()}

    def _ax_result(self, arg: object) -> tuple[bool, object]:
        """The app reports an actuation outcome; surface it to the user as an answer event."""
        if isinstance(arg, dict):
            self._emit("answer", {"text": str(arg.get("detail", "done."))})
        return True, {"ok": True}

    # ── ADR-024 Phase 2: bounded agent loop (navigate → re-perceive → act) ──
    def _navigate(self, target: str, question: str) -> str:
        """Open a vetted surface to reach an off-screen control and arm/advance the agent loop. Safe-
        open only (resolve_surface); the hop counter is the circuit breaker. Called for the first hop
        (from converse) and for subsequent hops (from `_drive_agent`)."""
        surface = resolve_surface(target)
        if surface is None:
            self._agent = None
            return f"I don't know how to get to '{target}'."
        if self._agent is None:
            self._agent = {"request": question or target, "hop": 1, "steps": 0,
                           "pending_epoch": None, "driven_epoch": -1, "deadline": time.time() + _AGENT_TTL}
        else:
            self._agent["hop"] += 1
            if self._agent["hop"] > _MAX_HOPS:
                self._agent = None
                return "I couldn't reach that — too many steps."
            self._agent["deadline"] = time.time() + _AGENT_TTL
            self._agent["driven_epoch"] = -1          # let the new surface's DOM be driven
        try:
            safespawn.run(["open", surface], capture_output=True, text=True, timeout=10)
        except Exception as exc:  # noqa: BLE001
            self._agent = None
            return f"Couldn't open settings for '{target}': {exc}"
        return f"Opening {target}…"

    def _drive_agent(self) -> None:
        """One agent-loop step on the settled DOM (driven from tick, so re-reads have finished). One
        step per distinct epoch; bounded by hops, steps, and the deadline. Actuation is ALWAYS routed
        through the consent gate; a give-up keeps waiting (deadline ends a stall)."""
        a = self._agent
        if a is None:
            return
        if time.time() > a["deadline"]:
            self._emit("answer", {"text": "I couldn't reach that in time."})
            self._agent = None
            return
        pe = a.get("pending_epoch")
        if pe is None or pe == a.get("driven_epoch"):
            return                                    # nothing new on screen to reason about yet
        a["driven_epoch"] = pe
        a["steps"] += 1
        if a["steps"] > _MAX_STEPS:
            self._emit("answer", {"text": "I couldn't complete that."})
            self._agent = None
            return
        step = agent_route(self._jarvis.agent_step(a["request"]))
        if step[0] == "act":                          # the goal control -> GATED consent path
            self._emit("answer", {"text": self._ax_dispatch_verb(step[1], step[2])})
            self._agent = None
        elif step[0] == "navigate":                   # another hop (circuit-broken in _navigate)
            self._emit("answer", {"text": self._navigate(step[1], a["request"])})
        # else give up: keep waiting for a more-rendered DOM until steps/deadline ends the loop

    def _axdump(self, arg: object) -> tuple[bool, object]:
        """Return the focused window's captured control tree (ADR-023) — to author/verify recipe
        matchers (role + title) against what macOS actually exposes, rather than guessing."""
        return True, {"text": (f"[{self._ax_app} epoch {self._ax_epoch}]\n" + self._ax_dom)
                              if self._ax_dom else "no controls captured yet (focus a window)."}

    def _ax_provider(self) -> str:
        """The focused-window controls block injected into the converse prompt (ADR-021). Empty when
        no app has pushed a snapshot (non-app clients / nothing focused)."""
        if not self._ax_dom:
            return ""
        return ("On-screen controls (focused window — you may act on these):\n"
                f"{self._ax_dom}\n"
                "To act, end your reply with [[DO: ax_press: <id>]], "
                "[[DO: ax_set_value: <id> <value>]], or [[DO: ax_set_checked: <id> 1]] (1=on, 0=off) "
                "using an id from the list above.")

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
        a = str(arg).strip().lower()
        result = self._flow.cmd(str(arg))
        if a in ("on", "off"):                          # ADR-048: persist the choice so it survives a restart
            self._sentinel_store.set_enabled(a == "on")
        return True, {"text": result}

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

    # ── ADR-056: General Mode (Dual-Brain) — the off-loop cloud answer path ──
    _CLOUD_SYSTEM = ("You are JARVIS in General Mode — a concise, accurate assistant. Answer the user's "
                     "question directly. Do not claim to have access to their files, memory, or device.")
    # The cloud's answer is fed back through the SAME firewall to extract symbolic claims for local NARS —
    # the reason General Mode exists (a frontier model makes the local vault smarter). Firewall inputs:
    # [this fixed system prompt] + [the cloud's own answer]. No private store is ever attached.
    _EXTRACT_SYSTEM = ("Extract the factual claims stated in the text as structured JSON: "
                       "subject-relation-object (RelationClaim) and subject-property (PropertyClaim). "
                       "Assert ONLY what the text states. If nothing factual is asserted, return an empty list.")

    def _cloud_ask(self, arg: object) -> tuple[bool, object]:
        """General Mode: answer via the user's cloud brain, OFF the select loop. The API key is passed
        per-request (ADR-056: the daemon never persists it); it lives only in this in-flight job's closure
        and is gone when the job completes. Returns an immediate ack; the answer arrives as a
        `cloud_answer` event so chat / sensing / the Mirror keep flowing while the cloud is thinking."""
        if not isinstance(arg, dict):
            return False, {"text": "cloud_ask expects {text, key, provider?, model?}"}
        text = str(arg.get("text", "")).strip()
        key = str(arg.get("key", ""))
        provider = str(arg.get("provider", "openai")) or "openai"
        model = str(arg.get("model", ""))
        if not text:
            return False, {"text": "cloud_ask expects a non-empty 'text'"}
        if not key:
            return False, {"text": "No API key for Cloud mode — add one, or stay On-device."}
        if not hasattr(self._llm, "cloud_complete"):
            return False, {"text": "Cloud brain not wired in this build — staying On-device."}

        from cloud_egress import CloudRequest
        from service.cloud_job import CloudJob
        req = CloudRequest(system=self._CLOUD_SYSTEM, user=text)
        # The closure runs in the CloudJob's background thread. It captures `key` locally (never stored on
        # the session) and uses the thread-safe one-shot path (no shared-context race).
        llm = self._llm
        job = CloudJob(lambda: llm.cloud_complete(req, key=key, provider=provider, model=model))
        self._token += 1
        token = self._token
        # The key/model ride along the in-flight pipeline entry (ask -> extract) only — dropped when the
        # pipeline ends. Still ephemeral and never persisted; this is the "life of one request" window.
        self._cloud_jobs[job.fileno()] = {"job": job, "token": token, "provider": provider,
                                          "key": key, "model": model, "kind": "ask"}
        self._persona_loop.observe(text, "user")          # the question is the user's, regardless of brain
        return True, {"status": "thinking", "token": token, "provider": provider}

    def _read_cloud(self, fd: int) -> None:
        entry = self._cloud_jobs.get(fd)
        if entry is None:
            return
        job = entry["job"]
        if not job.ready():
            return
        res = job.result()
        del self._cloud_jobs[fd]
        job.close()
        if entry["kind"] == "extract":                    # phase 2: the cloud's claims -> local vault
            self._ingest_cloud_claims(res, entry)
            return
        # phase 1: the cloud's answer
        if res is not None and res.ok:
            self._persona_loop.observe(res.text, "assistant")
            self._emit("cloud_answer", {"token": entry["token"], "ok": True,
                                        "provider": entry["provider"], "text": res.text})
            self._spawn_cloud_extraction(res.text, entry)   # ADR-056: the cloud feeds the symbolic vault
        else:
            kind = res.kind if res is not None else "network"
            err = res.error if res is not None else "The cloud call failed."
            self._emit("cloud_answer", {"token": entry["token"], "ok": False,
                                        "provider": entry["provider"], "kind": kind, "error": err})

    def _spawn_cloud_extraction(self, answer_text: str, entry: dict) -> None:
        """Phase 2 of the pipeline: route the cloud's OWN answer back through the egress seam (firewall +
        strict claims schema) to mine RelationClaim/PropertyClaim objects — OFF the loop, like phase 1."""
        text = (answer_text or "").strip()
        if not text:
            return
        from cloud_egress import CloudRequest
        from language.multiplexer import CLAIMS_JSON_SCHEMA
        from service.cloud_job import CloudJob
        llm, key = self._llm, entry["key"]
        provider, model = entry["provider"], entry["model"]
        req = CloudRequest(system=self._EXTRACT_SYSTEM, user=text, json_schema=CLAIMS_JSON_SCHEMA)
        job = CloudJob(lambda: llm.cloud_complete(req, key=key, provider=provider, model=model))
        # key/model are captured in the closure only — NOT stored on the extract entry, so they vanish
        # when this final leg completes (credential-stateless beyond the active request).
        self._cloud_jobs[job.fileno()] = {"job": job, "token": entry["token"],
                                          "provider": provider, "kind": "extract"}

    def _ingest_cloud_claims(self, res, entry: dict) -> None:
        """The payoff: cloud-extracted claims become PERMANENT local symbolic memory via the same durable
        sink as `learn`/`tell` (L1 ONA + L2 store). Runs on the main loop — ONA is single-owner. A bad
        single claim is skipped, never crashes the daemon."""
        if res is None or not res.ok:
            return
        import json
        from language import claims_to_narsese, parse_claims
        try:
            obj = json.loads(res.text or "{}")
            claims = parse_claims(json.dumps(obj.get("claims", [])))   # unwrap object-root -> bare array
        except Exception:  # noqa: BLE001 — malformed extraction -> the answer still stands
            return
        learned: list[str] = []
        for narsese in claims_to_narsese(claims):
            try:
                if self._jarvis.tell(narsese):
                    learned.append(narsese)
            except Exception:  # noqa: BLE001 — a single malformed/rejected claim never aborts the batch
                continue
        if learned:
            self._emit("cloud_learned", {"token": entry["token"], "count": len(learned), "narsese": learned})

    def _egress_log(self, arg: object) -> tuple[bool, object]:
        """ADR-056 Privacy Receipts: the auditable record of everything that has left the machine this
        session. Plain-language receipts (what was asked, to whom) — the byte count is a detail, not the
        headline. Importantly, this reads the egress seam's own log; it cannot expose private stores."""
        import cloud_egress
        receipts = [{"t": r["t"], "provider": r["provider"], "bytes": r["bytes"], "asked": r["preview"]}
                    for r in cloud_egress.egress_log()]
        return True, {"receipts": receipts, "count": len(receipts)}

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
        fds += list(self._cloud_jobs)           # ADR-056: in-flight off-loop cloud calls
        fds += self._overnight.extra_fds()      # ADR-052: the offloaded summary worker's stdout
        return fds

    def handle_fd(self, fd: int) -> None:
        if fd in self._voice_jobs:
            self._read_voice(fd)
        elif fd in self._cloud_jobs:             # ADR-056: drain a completed cloud answer
            self._read_cloud(fd)
        elif fd in self._overnight.extra_fds():  # ADR-052: drain the detached summary worker
            self._overnight.handle_fd(fd)
        elif fd == self._flow.fileno():
            self._flow.read()

    def tick(self) -> None:
        """Periodic M2 system-sentinel poll (CPU/mem -> surprise -> alert event), plus the consent
        expiry sweep (ADR-020): overdue requests default-resolve and emit `consent_closed`."""
        self._consent.sweep(time.time())
        if self._pending_nav is not None and time.time() > self._pending_nav["deadline"]:
            self._emit("answer", {"text": "I couldn't find that control to set."})
            self._pending_nav = None
        self._drive_agent()   # ADR-024 P2: advance an active agent loop on the settled DOM
        try:
            self._habit_loop.propose_due()   # ADR-026: offer an armed habit for the current hour-bucket
        except Exception as exc:  # noqa: BLE001 — a habit-tick hiccup must never kill the loop
            self._emit("alert", {"text": f"[habit error] {exc}"})
        try:
            self._overnight.advance()        # ADR-031: advance the overnight batch run by one task
        except Exception as exc:  # noqa: BLE001 — an overnight hiccup must never kill the loop
            self._emit("alert", {"text": f"[overnight error] {exc}"})
        try:
            idle = (time.time() - self._last_request_at) >= IDLE_SECONDS   # ADR-036: persona ingestion,
            self._persona_loop.tick(idle, overnight_active=self._overnight.active)  # idle-gated, batched
        except Exception as exc:  # noqa: BLE001 — a persona hiccup must never kill the loop
            self._emit("alert", {"text": f"[persona error] {exc}"})
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
        self._habit_store.close()
        self._persona_brain.close()      # ADR-036: tear down the isolated persona ONA
        self._persona_store.close()


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
