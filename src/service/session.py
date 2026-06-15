"""The headless JARVIS session — all reasoning/wiring, zero terminal I/O.

This is the brain behind the socket: it builds the core (models, gate, grounding, the two brains,
voice, executor) and exposes `dispatch(cmd, arg) -> (ok, body)` returning plain JSON-able data, plus
async work pushed through `on_event`. Both the terminal console and the future SwiftUI app are dumb
clients of this same surface, so reasoning logic can never be polluted by — or duplicated in — a UI.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
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
from summaries import SummaryArchive
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
# ADR-033: kinds offered in the Activity tab's task palette — overnight-appropriate (excludes ax/agent/habit,
# which need live GUI context or aren't tasks). work/query/diag -> Autonomous; argv/nav -> Held.
_ACTIVITY_KINDS = ("work", "query", "diag", "argv", "nav")
# Sprint 5 (context envelope): a pasted document too long for the on-device window is chunked through the
# SummaryJob pipeline instead of overflowing the model. ~4 chars/token (English) is a coarse gate, not an
# exact count — the precise overflow is still caught from the model's own error. _CTX_RESERVE leaves room
# for the system prompt + injected memory + the response budget below n_ctx.
_CTX_RESERVE = 2048
_SUMMARY_INTENT = re.compile(r"\b(summar(?:y|ise|ize|ies)|brief|tl;?dr|sum (?:it|this) up|recap|digest|gist)\b", re.I)
_OVERFLOW_RE = re.compile(r"Requested tokens \((\d+)\) exceed context window")
# Phase 2: the ONE general tool the cloud brain may call for live data. No weather/news special-casing —
# it decides when a question needs the world. Executed by Session._web_search (the hardened DuckDuckGo
# fetch the local research already uses), in the off-loop CloudJob thread.
_SEARCH_WEB = {
    "name": "search_web",
    "description": ("Search the web for CURRENT or live information — weather, news, prices, scores, "
                    "recent events, or anything past your training cutoff — and read the top results. "
                    "Call this whenever answering accurately needs up-to-date or real-world data."),
    "parameters": {"type": "object",
                   "properties": {"query": {"type": "string", "description": "the web search query"}},
                   "required": ["query"]},
}


class Session:
    def __init__(self, db_path: str = "jarvis.db", on_event: EventSink | None = None) -> None:
        self._emit = on_event or (lambda kind, body: None)
        self._db_path = db_path                  # Slice 3a: the triage worker writes the param store here
        self._store = MemoryStore(db_path)
        self._brain = Brain(cycles_per_step=50)
        self._act_buf: list[str] = []
        self._executor = build_air_gapped_executor(sink=lambda t: self._act_buf.append(str(t)))
        from .local_brain import LocalBrain
        # ADR-057: wrap the one local model context in the single-owner serializer. EVERY caller below is
        # injected the SAME LocalBrain, so all inference is mutually exclusive (the llama context is
        # non-reentrant) and the long Tier-2 decode can run OFF the select() loop.
        self._localbrain = LocalBrain(make_claim_source())
        llm = self._localbrain
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
        # ADR-058: the durable archive of briefed document summaries. The runner appends to it via the
        # on_summary hook when a `summarize_file` task completes; the Canvas Summary tab reads it back.
        self._summaries = SummaryArchive(db_path)
        self._backfill_summaries()
        self._overnight = OvernightRunner(
            self._overnight_queue, self._held_ledger, self._actions, self._emit,
            on_summary=self._on_summary, on_idle_maintenance=self._sweep_l2)
        # v1.24.0: Passive Context Ingestion — the FSEvents edge. Opt-in (only when NARS_JARVIS_WATCH_DIR is
        # set, so tests/normal runs spawn nothing); a Swift helper crushes filesystem noise at the kernel
        # callback and flushes bounded candidate batches we drain in handle_fd. CAPTURE only — the ingest is
        # the overnight runner's deferred job.
        self._ingest_watch = None
        self._ingest_drain = None
        _watch_dir = os.environ.get("NARS_JARVIS_WATCH_DIR")
        if _watch_dir:
            from sentinel.ingest_watch import IngestWatcher
            self._ingest_watch = IngestWatcher(db_path, _watch_dir)
            if not self._ingest_watch.start():            # swiftc/source unavailable -> degrade silently
                self._ingest_watch = None
        if self._ingest_watch is not None:                # Sprint 2: the overnight drain over the captured queue
            from sentinel.ingest_drain import IngestDrain
            self._ingest_watch.queue.reset_running()      # crash recovery: re-queue a row stranded 'running'
            self._ingest_drain = IngestDrain(self._ingest_watch.queue, self._ingest_watch.watch_root,
                                             ingest_fn=self._ingest_to_overnight)
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
        self._jarvis = Jarvis(Translator(llm, embedder=embedder, cache=grounding,
                                         alias_sink=self._record_local_alias),  # Gate 2: local alias harvest
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
                              navigate=self._navigate,  # bounded agent loop (ADR-024 P2)
                              lexicon_sink=self._record_lexicon)  # ADR-056/Gate 2: ingest -> L2 lexicon
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
        from retrieval.lexicon import LexiconStore     # ADR-056/Gate 2: the L2 namespace index
        self._lexicon = LexiconStore(db_path)          # populated at ingest (terms + aliases)
        from retrieval.metrics import RecallMetrics    # ADR-056/Gate 2 §8: content-free compounding telemetry
        self._recall_metrics = RecallMetrics(db_path)
        self._loop_gap_max = 0.0                 # Gate 1: worst select()-loop gap during a cloud call
        self._recall_jobs: dict[int, object] = {}      # ADR-056/Gate 2: fd -> in-flight off-loop Stage-4 worker
        self._file_jobs: dict[int, dict] = {}          # on-device file eval: fd -> in-flight local SummaryJob -> chat
        self._learn_jobs: dict[int, dict] = {}         # v1.24.0 Sprint 3: fd -> in-flight off-loop Narsese distillation
        self._triage_jobs: dict[int, dict] = {}        # Slice 3a: fd -> in-flight off-loop deviation scan
        self._last_scans: dict[str, dict] = {}         # Slice 3a: doc -> last deviation_scan body (late-join pull)
        from triage.paramstore import ParamStore       # Slice 4: read handle for dedup + corpus-size denominator
        self._paramstore = ParamStore(self._db_path)   # (the off-loop worker WRITES via its own WAL connection)
        self._last_corpus: dict = {}                   # Slice 4: last corpus_progress body (coalesce + late-join)
        self._converse_pending: dict[int, dict] = {}   # ADR-057: token -> {state, question, voice} awaiting decode
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
            "lexicon_stats": self._lexicon_stats,                         # ADR-056/Gate 2: inspect the L2 namespace index
            "recall": self._recall,                                       # ADR-056/Gate 2: hybrid retrieval + STAMP provenance
            "file_summarize": self._file_summarize,                       # on-device (private) file evaluation -> chat
            "triage_scans": self._triage_scans,                           # Slice 3a: latest deviation_scan per doc (late-join pull)
            "corpus_ingest": self._corpus_ingest,                         # Slice 4: bulk-ingest a folder of contracts
            "corpus_status": self._corpus_status,                         # Slice 4: cumulative ingest progress (late-join)
            "metrics": self._metrics_summary,                             # ADR-056 §8: compounding-value readout (Identity tab)
            "intent_parse": self._intent_parse,                           # ADR-054: NL -> validated Canvas intent
            "catalog_schema": self._catalog_schema,                       # ADR-033: palette for the canvas
            "briefing": self._briefing, "briefing_resolve": self._briefing_resolve,  # ADR-031: morning
            "briefing_dismiss_done": self._briefing_dismiss_done,         # ADR-033: clear completed
            "summary_list": self._summary_list, "summary_get": self._summary_get,  # ADR-058: Summary archive
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
            answer = self._jarvis.ask(text)            # a fast ONA query — no model, stays synchronous
            return True, {"text": f"answer: {answer}" if answer is not None else "no answer in memory."}
        self._persona_loop.observe(text, "user")   # ADR-036: buffer the utterance for idle batch learning
        return self._begin_converse(text, voice=False)

    # ── ADR-057: Tier-2 converse OFF the select() loop (the 8s-decode beachball fix) ──
    def _begin_converse(self, text: str, voice: bool) -> tuple[bool, object]:
        """Assemble the prompt on the main thread (ONA/SQLite), then hand the 512-token decode to the
        LocalBrain worker. Returns a fast ack; the answer arrives later as a `local_answer` event. With no
        model wired, `converse_begin` returns None and we answer synchronously from the grounded path."""
        # Sprint 5: a paste too long for the on-device window + a summarize/brief intent -> chunk it via
        # the SummaryJob pipeline (same path as an attached doc) instead of overflowing the model.
        n_ctx = int(getattr(self._localbrain, "n_ctx", 8192) or 8192)
        if self._est_tokens(text) > (n_ctx - _CTX_RESERVE) and self._wants_summary(text):
            return self._autoroute_long_summary(text)
        state = self._jarvis.converse_begin(text)
        if state is None:
            return True, {"text": self._jarvis.converse_fallback(text)}
        self._token += 1
        token = self._token
        self._converse_pending[token] = {"state": state, "question": text, "voice": voice}
        self._loop_gap_max = 0.0                          # start the loop-liveness meter for this decode
        self._localbrain.submit(token, state["system"], state["user"], 512)
        return True, {"status": "thinking_local", "token": token}

    @staticmethod
    def _est_tokens(text: str) -> int:
        return len(text) // 4                            # ~4 chars/token (English) — a coarse over-length gate

    @staticmethod
    def _wants_summary(text: str) -> bool:
        # The instruction lives at the ends ("brief this: <paste>" / "<paste> … summarize"), not buried in
        # the body — so a legal case mentioning "summary judgment" mid-text doesn't false-trigger.
        probe = (text[:200] + " " + text[-200:]).lower()
        return bool(_SUMMARY_INTENT.search(probe))

    def _autoroute_long_summary(self, text: str) -> tuple[bool, object]:
        """Sprint 5: stage an over-length summarize-paste to a temp .txt and run it through the chunked
        SummaryJob pipeline (same as an attached doc), so the user needn't save their clipboard by hand.
        Starting the runner emits `overnight_started` -> the Activity tab refreshes and shows the task."""
        approx_k = max(1, round(self._est_tokens(text) / 1000))
        try:
            d = os.path.join(tempfile.gettempdir(), "jarvis-pastes")
            os.makedirs(d, exist_ok=True)
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="pasted-", dir=d)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:  # noqa: BLE001 — can't stage the file -> fall back to the honest message
            sys.stderr.write(f"[autoroute] could not stage long paste: {exc}\n")
            return True, {"text": f"That's ~{approx_k}k tokens — too long for the on-device model. "
                                  "Attach it as a file to summarize it, or switch to ☁️ Cloud."}
        self._overnight_queue.enqueue("summarize_file", path)
        self._overnight.start()
        return True, {"text": f"📋 That's ~{approx_k}k tokens — too long to read in one pass, so I chunked it "
                              "and sent it to the Activity tab to summarize. Watch it there."}

    def _drain_converse(self) -> None:
        """select() flagged the LocalBrain's self-pipe: one or more decodes finished. Post-process each on
        the main thread (ONA/SQLite writes), emit the answer, and — if it came from voice — speak it."""
        for token, ok, raw in self._localbrain.results():
            entry = self._converse_pending.pop(token, None)
            if entry is None:
                continue
            if ok:
                final = self._jarvis.converse_resume(entry["state"], raw)
            else:
                sys.stderr.write(f"[localbrain] decode failed: {raw}\n")
                m = _OVERFLOW_RE.search(str(raw))         # Sprint 5: an honest overflow message, not the
                if m:                                     # misleading grounded "couldn't read that" fallback
                    approx_k = max(1, round(int(m.group(1)) / 1000))
                    final = (f"That's ~{approx_k}k tokens — too long for the on-device model. "
                             "Attach it as a file to summarize it, or switch to ☁️ Cloud.")
                else:
                    final = self._jarvis.converse_fallback(entry["question"])
            gap_ms = round(self._loop_gap_max * 1000, 1)   # ADR-057 liveness reading for this decode
            sys.stderr.write(f"[tier2] local decode done; worst select()-loop gap while in flight: {gap_ms} ms\n")
            self._persona_loop.observe(final, "assistant")
            try:                                          # Phase 3a: attach the grounded derivation if ONA has one
                explanation = self._jarvis.explain(entry["question"])
            except Exception:  # noqa: BLE001 — explainability is additive; it must never break the answer
                explanation = None
            self._emit("local_answer", {"token": token, "text": final, "loop_max_gap_ms": gap_ms,
                                        "explanation": explanation})
            if entry["voice"]:
                self._emit("answer", {"text": final})
                speak(strip_acknowledgment(final))

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
        """Approve/deny a held action from the Canvas. `id` is the overnight_queue row id (what the
        Activity tab shows — its rows come from the queue, not the held-ledger). On approve, run it
        NOW — the click IS the consent gate — then stamp the queue row terminal so the card leaves
        the held state; on deny, record 'Declined' (also terminal, so it can be Cleared)."""
        if not isinstance(arg, dict):
            return False, {"text": "usage: briefing_resolve {id, accepted}"}
        tid, accepted = int(arg.get("id", 0)), bool(arg.get("accepted"))
        row = self._held_ledger.resolve_by_task(tid, accepted)
        if row is None:
            return True, {"text": "no held action with that id (already resolved?)."}
        if not accepted:
            self._overnight_queue.mark(tid, "done", result="Declined — not run.")
            return True, {"text": f"declined: {row['action']}"}
        result = self._actions.perform(row["action"], row["arg"])   # human just approved -> execute
        self._overnight_queue.mark(tid, "done", result=str(result))
        return True, {"text": result}

    # ── ADR-033: Batch Canvas (palette schema, batch commit, clear-completed) ──
    def _catalog_schema(self, _arg: object) -> tuple[bool, object]:
        """The Batch Canvas palette: overnight-appropriate actions annotated with their autonomous/held
        tag. The autonomy call lives here (session imports both actions + overnight), keeping the catalog
        ignorant of overnight semantics and the Swift UI free of business logic."""
        actions = [{**a, "autonomous": safe_autonomous(resolve_action(a["name"]))}
                   for a in catalog_schema() if a["kind"] in _ACTIVITY_KINDS]
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
        acts = [a for a in catalog_schema() if a["kind"] in _ACTIVITY_KINDS]
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

    # ── ADR-058: the Canvas Summary tab — durable archive of briefed document summaries ──
    def _backfill_summaries(self) -> None:
        """One-time, idempotent: seed the archive from any already-done `summarize_file` queue rows
        (briefed before the archive existed) so the Summary tab isn't empty on first launch."""
        for r in self._overnight_queue.list_all():
            if (r["status"] == "done" and r["action"] == "summarize_file" and r["result"]
                    and not self._summaries.has(r["arg"], r["result"])):
                self._summaries.add(os.path.basename(r["arg"]), r["arg"], r["result"])

    def _summary_list(self, _arg: object) -> tuple[bool, object]:
        """The Summary tab's list: newest-first {id, source_name, created_at, chars} (no body)."""
        return True, {"rows": self._summaries.list()}

    def _summary_get(self, arg: object) -> tuple[bool, object]:
        """Fetch one archived summary's full text (the Swift client renders it to a PDF and opens it)."""
        sid = int(arg.get("id", 0)) if isinstance(arg, dict) else int(arg or 0)
        row = self._summaries.get(sid)
        if row is None:
            return True, {"text": "no summary with that id."}
        return True, {"id": row["id"], "source_name": row["source_name"],
                      "text": row["text"], "created_at": row["created_at"]}

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
            # default: treat the utterance as a question. ADR-057: a plain question decodes OFF the loop;
            # the spoken reply is emitted by `_drain_converse` when the decode lands (voice=True).
            raw = transcript.lstrip()
            if not ("-->" in raw or raw.startswith(("<", "("))):
                self._persona_loop.observe(transcript, "user")
                self._begin_converse(transcript, voice=True)
                return
            cmd, arg = "ask", transcript
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

    @staticmethod
    def _web_search(name: str, args: dict) -> str:
        """Phase 2 tool executor (runs in the CloudJob thread, off the select loop): execute one web search
        via the hardened, SSRF-guarded DuckDuckGo fetch the local research already uses (its own subprocess
        — network + headless work stays out of the daemon). Returns the result snippets, or an [ERROR: …]
        the model can react to. Never raises."""
        if name != "search_web":
            return f"[ERROR: unknown tool '{name}']"
        query = str(args.get("query", "")).strip() if isinstance(args, dict) else ""
        if not query:
            return "[ERROR: empty search query]"
        import safespawn
        web_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "actions", "web.py")
        try:
            r = safespawn.run([sys.executable, web_py, "search", query],
                              capture_output=True, text=True, timeout=20)
            out = (getattr(r, "stdout", "") or "").strip()
            return out or "[ERROR: web search returned nothing]"
        except Exception as exc:  # noqa: BLE001 — timeout / spawn failure -> a result, never a crash
            return f"[ERROR: web search failed: {exc}]"

    def _cloud_system(self) -> str:
        """The cloud system prompt + the user's CURRENT local date/time/timezone (Phase 1 table-stakes fix).
        The daemon already knows the clock — so the cloud brain should never refuse a time/date/'tomorrow'
        question for lack of it. Injects ONLY the clock: no habits, no app usage, no files — the privacy
        promise ('your memory, habits & files stayed on your Mac') is untouched."""
        now = datetime.now().astimezone()
        tz = now.tzname() or now.strftime("%z")
        return (self._CLOUD_SYSTEM + " The user's current local date and time is "
                f"{now.strftime('%A, %B %d, %Y at %I:%M %p')} {tz}. Use this for any time-, date-, or "
                "today/tomorrow-relative question — you already know it and never need to ask.")
    # The cloud's answer is fed back through the SAME firewall to extract symbolic claims for local NARS —
    # the reason General Mode exists (a frontier model makes the local vault smarter). Firewall inputs:
    # [this fixed system prompt] + [the cloud's own answer]. No private store is ever attached.
    _EXTRACT_SYSTEM = ("Extract the factual claims stated in the text as structured JSON: "
                       "subject-relation-object (RelationClaim) and subject-property (PropertyClaim). "
                       "Assert ONLY what the text states. If nothing factual is asserted, return empty lists. "
                       "Also return 'aliases': for any entity you wrote under a canonical name but the text "
                       "referred to by a different surface form (e.g. text 'SOL' -> canonical 'solana'), "
                       "emit {surface, canonical}. Omit aliases when the text used the canonical name.")

    def _record_lexicon(self, term: str) -> None:
        """Jarvis fires this with each committed canonical Narsese term -> populate the L2 lexicon. The
        lexicon only ever sees canonical atoms here (raw telemetry frames never reach it)."""
        from retrieval.lexicon_ingest import record_narsese_terms
        record_narsese_terms(self._lexicon, term, now=time.time())

    def _record_local_alias(self, surface: str, canonical: str) -> None:
        """The Translator fires this whenever LOCAL grounding resolves a surface form to a canonical atom
        (on-device embedder, no network) -> mirror it into the lexicon alias table. This is how the
        Private Vault learns the user's vocabulary without ever connecting Cloud."""
        self._lexicon.record_alias(surface, canonical, now=time.time())

    def _cloud_ask(self, arg: object) -> tuple[bool, object]:
        """General Mode: answer via the user's cloud brain, OFF the select loop. The API key is passed
        per-request (ADR-056: the daemon never persists it); it lives only in this in-flight job's closure
        and is gone when the job completes. Returns an immediate ack; the answer arrives as a
        `cloud_answer` event so chat / sensing / the Mirror keep flowing while the cloud is thinking."""
        if not isinstance(arg, dict):
            return False, {"text": "cloud_ask expects {text, key, provider?, model?, file?}"}
        text = str(arg.get("text", "")).strip()
        key = str(arg.get("key", ""))
        provider = str(arg.get("provider", "openai")) or "openai"
        model = str(arg.get("model", ""))
        # Optional attached document (the "+" button): the daemon reads the file (text/PDF) and folds its
        # content into the prompt. In Cloud mode this content LEAVES the machine — consistent with the
        # cloud toggle the user chose; the egress footer/receipt still apply.
        file_path = str(arg.get("file", "")).strip()
        if file_path:
            import os
            from actions.documents import read_file_text
            content = read_file_text(file_path)               # text/PDF; never raises; '⚠ ...' on problems
            if content.startswith("⚠"):
                return False, {"text": content}
            question = text or "Evaluate this document and summarize its key points, flagging anything notable."
            text = f"{question}\n\n[Attached file: {os.path.basename(file_path)}]\n\n{content[:24000]}"
        if not text:
            return False, {"text": "cloud_ask expects a non-empty 'text'"}
        if not key:
            return False, {"text": "No API key for Cloud mode — add one, or stay On-device."}
        if not hasattr(self._llm, "cloud_complete"):
            return False, {"text": "Cloud brain not wired in this build — staying On-device."}

        from cloud_egress import CloudRequest, ExternalTool
        from service.cloud_job import CloudJob
        # Phase 2: give the cloud brain a GENERAL web-search tool (no weather/news special-casing). It
        # decides when a question needs the live world; we just hand it the door. Phase 1's clock rides in
        # the system prompt. A file attachment is mutually exclusive with the tool (the doc is the context).
        tools = [] if file_path else [ExternalTool(**_SEARCH_WEB)]
        req = CloudRequest(system=self._cloud_system(), user=text, tools=tools)
        # The closure runs in the CloudJob's background thread, which also runs the tool-call loop — so the
        # web fetch is off the select() loop too. `key` is captured locally (never stored on the session).
        llm = self._llm
        job = CloudJob(lambda: llm.cloud_complete(req, key=key, provider=provider, model=model,
                                                  tool_executor=self._web_search))
        self._token += 1
        token = self._token
        # The key/model ride along the in-flight pipeline entry (ask -> extract) only — dropped when the
        # pipeline ends. Still ephemeral and never persisted; this is the "life of one request" window.
        self._cloud_jobs[job.fileno()] = {"job": job, "token": token, "provider": provider,
                                          "key": key, "model": model, "kind": "ask"}
        self._loop_gap_max = 0.0                     # start the Gate-1 loop-liveness meter for this call
        self._persona_loop.observe(text, "user")          # the question is the user's, regardless of brain
        return True, {"status": "thinking", "token": token, "provider": provider}

    # ── Gate 1/3 instrument: prove the select() loop never stalls while OFF-LOOP work is in flight ──
    def cloud_in_flight(self) -> bool:
        return bool(self._cloud_jobs)

    def offloop_in_flight(self) -> bool:
        """Any off-loop job running (a cloud call, a Stage-4 recall worker, OR a Tier-2 local decode) — the
        window during which the daemon measures the loop gap (Gate 1 = cloud, Gate 3 = recall IPC, ADR-057
        = the local 7B decode that used to beach-ball the loop)."""
        return bool(self._cloud_jobs) or bool(self._recall_jobs) or self._localbrain.busy

    def note_loop_gap(self, gap: float) -> None:
        """Fed by the daemon every loop iteration while off-loop work runs. The MAX gap is the real
        telemetry-drop signal: a healthy loop iterates on its poll cadence (faster under sensor activity);
        a loop blocked on a synchronous derivation/call would show a gap ≈ that work's duration."""
        if gap > self._loop_gap_max:
            self._loop_gap_max = gap

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
        gap_ms = round(self._loop_gap_max * 1000, 1)    # Gate-1 telemetry-liveness reading for this call
        sys.stderr.write(f"[gate1] cloud call done; worst select()-loop gap while in flight: {gap_ms} ms\n")
        if res is not None and res.ok:
            self._persona_loop.observe(res.text, "assistant")
            self._emit("cloud_answer", {"token": entry["token"], "ok": True, "provider": entry["provider"],
                                        "text": res.text, "loop_max_gap_ms": gap_ms})
            self._spawn_cloud_extraction(res.text, entry)   # ADR-056: the cloud feeds the symbolic vault
        else:
            kind = res.kind if res is not None else "network"
            err = res.error if res is not None else "The cloud call failed."
            self._emit("cloud_answer", {"token": entry["token"], "ok": False, "provider": entry["provider"],
                                        "kind": kind, "error": err, "loop_max_gap_ms": gap_ms})

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
        from retrieval.lexicon_ingest import record_alias_pairs
        try:
            obj = json.loads(res.text or "{}")
            claims = parse_claims(json.dumps(obj.get("claims", [])))   # unwrap object-root -> bare array
        except Exception:  # noqa: BLE001 — malformed extraction -> the answer still stands
            return
        # harvest surface->canonical aliases the cloud extractor yielded (terms populate via tell()->sink)
        record_alias_pairs(self._lexicon, obj.get("aliases", []), now=time.time())
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

    def _metrics_summary(self, arg: object) -> tuple[bool, object]:
        """ADR-056 §8: the compounding-value readout for the Cognitive Identity tab — FA-LGR, stamp-age
        depth, flywheel close rate, plus the period-over-period trend for the headline. Computed locally
        from content-free rows; nothing leaves the machine."""
        return True, {**self._recall_metrics.summary(), **self._recall_metrics.trend()}

    def _lexicon_stats(self, arg: object) -> tuple[bool, object]:
        """ADR-056/Gate 2: inspect the L2 lexicon — term count, and (if a mention is given) what it
        resolves to deterministically (exact term or top alias). Read-only."""
        mention = str(arg).strip() if arg else ""
        out: dict = {"term_count": self._lexicon.term_count()}
        if mention:
            out["mention"] = mention
            out["resolved"] = self._lexicon.resolve(mention)
            out["aliases"] = self._lexicon.resolve_alias(mention)
        return True, out

    def _recall(self, arg: object) -> tuple[bool, object]:
        """ADR-056/Gate 2: answer a question by HYBRID RETRIEVAL over the live L2 graph. Stages 0-3 (parse
        -> resolve -> FTS traverse -> rank) run synchronously here in single-digit ms; Stage 4 (the ONA
        derivation) runs OFF-LOOP in an ephemeral worker so a pathological deep query can't block the
        select loop — it's SIGKILL'd at a hard deadline and escalated. The answer/abstention arrives as a
        `recall_result` event. An immediate abstain (no local subgraph) returns synchronously."""
        query = str(arg).strip()
        if not query:
            return False, {"text": "usage: recall <question>"}
        from retrieval.pipeline import plan
        pr = plan(query, store=self._store, lexicon=self._lexicon, now=time.time())
        self._token += 1
        token = self._token
        topic = self._recall_metrics.topic_hash(pr.anchors + list(pr.targets))   # '' -> zero-anchor (excluded)
        if not pr.groundable:                            # Stage 0-3 abstained -> no worker; UI falls to Tier 2
            self._recall_metrics.record(topic, grounded=False)
            return True, {"grounded": False, "escalate": "cloud", "token": token,
                          "text": "I don't have enough local context to answer this — Ask Cloud?"}
        from service.recall_job import RecallJob
        job = RecallJob(pr.beliefs, pr.question, token)
        job.topic_hash = topic                            # carried to the completion metric
        self._recall_jobs[job.fileno()] = job
        self._loop_gap_max = 0.0                          # start the Gate-3 loop-liveness meter for this worker
        return True, {"status": "reasoning", "token": token}

    def _read_recall(self, fd: int) -> None:
        job = self._recall_jobs.get(fd)
        if job is None:
            return
        res = job.read()
        if res is None:
            return                                       # still accumulating the worker's output
        del self._recall_jobs[fd]
        job.cleanup()
        self._emit_recall(job.token, res, getattr(job, "topic_hash", ""))

    def _emit_recall(self, token: int, res: dict, topic: str = "") -> None:
        """Enrich the worker's STAMP from L2 (main thread owns the store), record the metric, and emit. No
        answer / no provenance / a timeout-kill all collapse to the same honest abstention."""
        gap_ms = round(self._loop_gap_max * 1000, 1)     # Gate-3 reading: worst loop gap during the worker
        sys.stderr.write(f"[gate3] recall worker done; worst select()-loop gap while in flight: {gap_ms} ms\n")
        if res.get("grounded") and res.get("answer"):
            from retrieval.pipeline import enrich_provenance
            prov = enrich_provenance(self._store, res["answer"], res.get("stamp", []))
            if prov:
                self._recall_metrics.record(topic, grounded=True, stamp_age_days=self._stamp_age_days(prov))
                self._emit("recall_result", {"token": token, "grounded": True, "answer": res["answer"],
                                             "truth": res.get("truth"), "provenance": prov,
                                             "loop_max_gap_ms": gap_ms})
                return
        self._recall_metrics.record(topic, grounded=False)
        self._emit("recall_result", {"token": token, "grounded": False, "escalate": "cloud",
                                     "loop_max_gap_ms": gap_ms,
                                     "text": "I don't have enough local context to answer this — Ask Cloud?"})

    @staticmethod
    def _stamp_age_days(provenance: list) -> float | None:
        """Stamp-Age Depth: age (days) of the OLDEST cited premise — the compounding signal."""
        ages = [p["learned_at"] for p in provenance if p.get("learned_at")]
        return None if not ages else max(0.0, (time.time() - min(ages)) / 86400.0)

    # ── the time-bomb (hard 5s ceiling on the Stage-4 worker) ──
    def next_recall_deadline(self):
        """Soonest in-flight worker deadline (monotonic), so the daemon can wake select() exactly then."""
        ds = [j.deadline for j in self._recall_jobs.values()]
        return min(ds) if ds else None

    def reap_expired_recalls(self, now: float) -> None:
        """SIGKILL + reap any worker past its deadline (no zombies) and escalate that query to Cloud."""
        for fd, job in list(self._recall_jobs.items()):
            if job.expired(now):
                job.kill()
                del self._recall_jobs[fd]
                self._emit_recall(job.token, {"grounded": False}, getattr(job, "topic_hash", ""))   # timeout

    def _do_shutdown(self, arg: object) -> tuple[bool, object]:
        """Emergency stop / kill switch: the daemon loop exits after replying, closing the brains,
        the sentinel, and the actuator. The single off-switch for the whole system."""
        self._shutdown = True
        return True, {"text": "shutting down"}

    def wants_shutdown(self) -> bool:
        return self._shutdown

    def _ingest_to_overnight(self, path: str) -> None:
        """Sprint 2 splice: a validated, changed captured file goes through the EXISTING off-loop SummaryJob
        chunker via the overnight queue (which archives it to the durable summary store the vault surfaces).
        CAPTURE -> drain -> the same pipeline an attached doc uses."""
        self._overnight_queue.enqueue("summarize_file", path)
        if not self._overnight.active:
            self._overnight.start()

    def _sweep_l2(self) -> None:
        """v1.24.0 Step 3 idle-maintenance hook: the overnight runner calls this when its queue drains
        (off the select() loop, on idle/AC). Reversibly tombstones stale, never-recalled passive beliefs
        so L2 stays lean. A single set-based UPDATE; failure is logged, never crashes the night."""
        try:
            n = self._store.sweep_passive()
        except Exception as exc:  # noqa: BLE001 — L2 hygiene must never kill the overnight run
            sys.stderr.write(f"[sweep] passive L2 sweep failed: {exc}\n")
            return
        if n:
            sys.stderr.write(f"[sweep] tombstoned {n} stale passive belief(s)\n")
            self._emit("l2_swept", {"tombstoned": n})   # content-free count only

    @staticmethod
    def _on_ac_power() -> bool:
        """Drives the deferred-payload budget: True = run heavy candidates. No battery sensor (desktop) ->
        treated as plugged in."""
        try:
            import psutil
            batt = psutil.sensors_battery()
            return batt is None or batt.power_plugged
        except Exception:  # noqa: BLE001
            return True

    # ── v1.24.0 Sprint 3: Narsese distillation (a completed summary -> beliefs in the vault) ──
    def _on_summary(self, path: str, text: str) -> None:
        """A SummaryJob finished: archive the summary (ADR-058) AND distill it into Narsese beliefs. The
        distillation's LLM extraction runs OFF the loop in a LearnJob; only the cheap L1+L2 commit lands
        here on the main thread, so the daemon stays sub-10ms."""
        self._summaries.add(os.path.basename(path), path, text)
        self._spawn_learn(text, source=path)
        self._spawn_triage(path)                       # Slice 3a: corpus-aware deviation scan (AC-gated)

    # ── Slice 3a: the off-loop deviation scan (extract salient params -> deviate vs the user's own corpus) ──
    def _triage_allowed(self) -> tuple[bool, str]:
        """The AC/consent gate. The 3x consensus extraction is computationally brutal, so it only runs on
        AC power. Consent is structural, not a per-doc prompt: the chat path is an explicit user attach; the
        passive path only reaches here once the user-opted-in watcher's summary cleared the (AC-gated)
        overnight runner. Both paths additionally pass this AC check at the triage spawn."""
        if not self._on_ac_power():
            return False, "on_battery"
        return True, ""

    def _spawn_triage(self, path: str, tid: int | None = None) -> None:
        """Off-loop deviation scan. `tid` set == a bulk-ingest task (Slice 4): the corpus drainer already
        owns the AC gate, so we skip the live deferred-event path and instead settle the queue row on
        completion. tid None == the live chat/watch path, which keeps its own AC gate + deferred event."""
        doc = os.path.basename(path)
        if not path or not os.path.isfile(path):
            if tid is not None:
                self._overnight_queue.mark(tid, "failed", result="missing file")
            return
        if tid is None:
            allowed, reason = self._triage_allowed()
            if not allowed:                            # deferred, never silently dropped (no auto-retry yet)
                body = {"doc": doc, "state": "deferred", "reason": reason}
                self._last_scans[doc] = body
                self._emit("deviation_scan", body)
                return
        from service.triage_job import TriageJob
        self._token += 1
        try:
            job = TriageJob(path, self._db_path, self._token)
        except Exception as exc:  # noqa: BLE001 — a spawn failure is logged, never crashes the daemon
            sys.stderr.write(f"[triage] could not start deviation worker: {exc}\n")
            if tid is not None:
                self._overnight_queue.mark(tid, "failed", result=str(exc))
                self._emit_corpus_progress()
            return
        self._triage_jobs[job.fileno()] = {"job": job, "doc": doc, "body": None, "tid": tid}

    def _read_triage_job(self, fd: int) -> None:
        """Drain a completed deviation scan. The expensive re-parse + extraction already ran off-loop; here we
        only relay the progressive-UI events (pending -> populated/empty) to clients on the main thread."""
        entry = self._triage_jobs.get(fd)
        if entry is None:
            return
        job = entry["job"]
        for tag, payload in job.read():
            if tag == "pending":
                n = payload.get("salient_count", 0) if isinstance(payload, dict) else 0
                self._emit("deviation_scan", {"doc": entry["doc"], "state": "pending", "salient_count": n})
            elif tag == "result":
                entry["body"] = payload if isinstance(payload, dict) else None
            elif tag == "error":
                sys.stderr.write(f"[triage] scan error ({entry['doc']}): {payload}\n")
            elif tag == "eof":
                del self._triage_jobs[fd]
                job.cleanup()
                body = entry["body"]
                errored = body is None
                if errored:                            # worker errored before a result -> terminal empty
                    body = {"doc": entry["doc"], "doc_id": "", "salient_count": 0,
                            "state": "empty", "findings": []}
                self._last_scans[entry["doc"]] = body
                self._emit("deviation_scan", body)
                if entry.get("tid") is not None:        # Slice 4: settle the bulk queue row + advance progress
                    self._overnight_queue.mark(entry["tid"], "failed" if errored else "done",
                                               result=body.get("state", ""))
                    self._emit_corpus_progress()

    def _triage_scans(self, arg) -> tuple[bool, object]:
        """Pull the latest deviation_scan body per document (late-join: a client that connects after a scan
        finished still gets current state). Push is the live plane; this is the catch-up read."""
        return True, {"rows": list(self._last_scans.values())}

    # ── Slice 4: onboarding bulk ingest (cold-start mitigation — populate the deviation baseline) ──
    def _corpus_body(self) -> dict:
        from service.corpus import progress_body
        return progress_body(self._overnight_queue.list_all(), len(self._paramstore.known_doc_ids()))

    def _emit_corpus_progress(self) -> None:
        body = self._corpus_body()
        if body != self._last_corpus:                  # coalesce: only broadcast when the counters actually move
            self._last_corpus = body
            self._emit("corpus_progress", body)

    def _corpus_ingest(self, arg) -> tuple[bool, object]:
        """Connect a folder of historical contracts -> queue each valid, not-already-ingested PDF for the
        off-loop triage worker. TRIAGE-ONLY (params -> baseline); never the summarize/learn pipeline."""
        path = arg.get("path", "") if isinstance(arg, dict) else str(arg or "")
        path = str(path).strip()
        if not path or not os.path.isdir(path):
            return False, {"text": f"Not a folder: {path}"}
        from service.corpus import scan_folder
        from triage.devscan import file_doc_id
        scan = scan_folder(path, self._paramstore.known_doc_ids(), file_doc_id)
        for p in scan.to_enqueue:
            self._overnight_queue.enqueue("triage_file", p)
        if scan.truncated:
            sys.stderr.write(f"[corpus] {scan.truncated} file(s) over the per-import cap were not queued\n")
        # NOTE: do NOT start the OvernightRunner here — triage_file is drained by the tick-driven
        # _drain_corpus (a separate consumer); the runner explicitly skips triage_file (_NOT_OURS).
        self._emit_corpus_progress()
        return True, {"queued": len(scan.to_enqueue), "skipped_dup": scan.skipped_dup,
                      "skipped_invalid": scan.skipped_invalid, "truncated": scan.truncated,
                      "corpus_size": len(self._paramstore.known_doc_ids())}

    def _corpus_status(self, arg) -> tuple[bool, object]:
        """Late-join pull of the cumulative corpus-ingest progress (a client connecting mid-drain)."""
        return True, self._corpus_body()

    def _heavy_inference_active(self) -> bool:
        """Memory firewall (Slice 4 hardening): True when a heavy model context is already resident/active —
        the daemon's Metal decode, an off-loop SummaryJob, or a distillation worker. The corpus drain yields
        to these so only ONE heavy model is ever active at a time (19 GB unified-memory safety; the freeze)."""
        return self._localbrain.busy or self._overnight.offloading or bool(self._learn_jobs)

    def _drain_corpus(self) -> None:
        """AC-gated, SERIAL bulk-ingest drain (one triage_file at a time — concurrent model workers would
        thrash). Blocks (auto-resumes next idle tick) while another heavy model context is active. On battery
        it idles; the durable queue + reset_running() resume it on AC / after a restart."""
        if not self._on_ac_power():
            return
        if self._heavy_inference_active():     # memory firewall: yield to the primary/live model context
            return
        if any(e.get("tid") is not None for e in self._triage_jobs.values()):   # one bulk scan at a time
            return
        from service.corpus import next_triage_task
        task = next_triage_task(self._overnight_queue.list_all())
        if task is None:
            return
        self._overnight_queue.mark(task["id"], "running")
        self._spawn_triage(task["arg"], tid=task["id"])

    def _spawn_learn(self, text: str, source: str = "") -> None:
        if not text or not text.strip():
            return
        from service.learn_job import LearnJob
        self._token += 1
        try:
            job = LearnJob(text, self._token, source=source)
        except Exception as exc:  # noqa: BLE001 — a spawn failure is logged, never crashes the daemon
            sys.stderr.write(f"[learn] could not start distillation worker: {exc}\n")
            return
        self._learn_jobs[job.fileno()] = {"job": job, "narsese": [], "source": source}

    def _read_learn_job(self, fd: int) -> None:
        """Drain a completed distillation. The expensive extraction already ran off-loop; here we only
        commit the returned statements to L1 ONA + L2 store via the SAME durable sink as tell()/the cloud
        flywheel (single-owner main thread; a bad single claim is skipped, never aborts the batch)."""
        entry = self._learn_jobs.get(fd)
        if entry is None:
            return
        job = entry["job"]
        for tag, payload in job.read():
            if tag == "result":
                entry["narsese"] = payload if isinstance(payload, list) else []
            elif tag == "error":
                sys.stderr.write(f"[learn] extraction error ({entry['source']}): {payload}\n")
            elif tag == "eof":
                del self._learn_jobs[fd]
                job.cleanup()
                learned: list[str] = []
                for narsese in entry["narsese"]:
                    try:
                        # v1.24.0 Step 3: distilled claims are DOCUMENT-derived (FSEvents ingest + file
                        # summaries), never user-asserted -> source='passive'. They land in L2 only at the
                        # corroboration floor, bypassing ONA L1, and are subject to the decay sweep.
                        if self._jarvis.tell(str(narsese), source="passive"):
                            learned.append(str(narsese))
                    except Exception:  # noqa: BLE001 — one malformed/rejected claim never aborts the batch
                        continue
                if learned:
                    self._emit("learned", {"source": entry["source"], "count": len(learned),
                                           "narsese": learned})

    # ── On-device (private) file evaluation: local summarizer -> chat ──
    def _file_summarize(self, arg):
        """Evaluate an attached document with the LOCAL model — the private-mode default. Spawns the same
        off-loop CPU summary worker the overnight runner uses, but routes its result to chat. The file is
        read by the worker on this machine; it never leaves the Mac. Off the select() loop, so a large PDF
        can't beach-ball the daemon."""
        path = arg.get("path", "") if isinstance(arg, dict) else str(arg or "")
        path = str(path).strip()
        if not path:
            return False, {"text": "file_summarize expects a {path}."}
        if not os.path.isfile(path):
            return False, {"text": f"No such file: {path}"}
        from service.summary_job import SummaryJob
        self._token += 1
        token = self._token
        try:
            job = SummaryJob(path, token, action="summarize_file")
        except Exception as exc:  # noqa: BLE001 — a spawn failure is reported, never crashes the daemon
            return False, {"text": f"Couldn't start the local summarizer: {exc}"}
        # Mirror this chat read into the overnight queue as a LIVE job so it's visible in Activity › Now
        # (running) -> Log (done). It is chat-managed, NOT run by the overnight runner: marked 'running'
        # immediately so next_pending (pending-only) never double-processes it. A crash leaves it 'running'
        # -> reset_running reverts it to pending -> it is gracefully re-summarized on the next overnight run.
        tid = self._overnight_queue.enqueue("summarize_file", path)
        self._overnight_queue.mark(tid, "running", result="reading…")
        self._emit("overnight_progress", {"id": tid, "action": "summarize_file", "status": "running"})
        self._file_jobs[job.fileno()] = {"job": job, "token": token, "path": path, "tid": tid,
                                         "name": os.path.basename(path), "text": None, "error": None}
        return True, {"status": "reading", "token": token, "name": os.path.basename(path)}

    def _read_file_job(self, fd: int) -> None:
        entry = self._file_jobs.get(fd)
        if entry is None:
            return
        job = entry["job"]
        for tag, payload in job.read():
            if tag == "result":
                entry["text"] = payload if isinstance(payload, str) else str(payload)
            elif tag == "error":
                entry["error"] = payload if isinstance(payload, str) else str(payload)
            elif tag == "eof":
                del self._file_jobs[fd]
                job.cleanup()
                tid = entry.get("tid")
                if entry["text"]:
                    self._emit("file_result", {"token": entry["token"], "ok": True,
                                               "name": entry["name"], "text": entry["text"]})
                    if tid is not None:                       # close the live Activity row: running -> done
                        self._overnight_queue.mark(tid, "done", result=entry["text"])
                        self._emit("overnight_progress", {"id": tid, "action": "summarize_file",
                                                          "status": "done"})
                    # v1.24.0: close the loop — a chat-attached document must LAND in the permanent vault,
                    # not be discarded after the chat bubble. _on_summary archives it SYNCHRONOUSLY (cheap
                    # SQLite write -> Activity › Summary shows it was received) and spawns the off-loop
                    # LearnJob for the heavy 3x-pass guarded distillation ASYNCHRONOUSLY — so the chat reply
                    # is never blocked by the consensus extraction (ADR: decouple chat latency from the
                    # distillation pipeline).
                    try:
                        self._on_summary(entry["path"], entry["text"])
                    except Exception as exc:  # noqa: BLE001 — landing the doc must never break the reply
                        sys.stderr.write(f"[file] could not land '{entry['name']}' in the vault: {exc}\n")
                else:
                    self._emit("file_result", {"token": entry["token"], "ok": False, "name": entry["name"],
                                               "text": entry["error"] or "The local model couldn't read or "
                                               "summarize that file."})
                    if tid is not None:                       # close the live Activity row: running -> failed
                        self._overnight_queue.mark(tid, "failed", result=entry["error"] or "read failed")
                        self._emit("overnight_progress", {"id": tid, "action": "summarize_file",
                                                          "status": "failed"})

    # ── select-loop hooks for the daemon (sensor pipe + voice jobs) ────
    def extra_fds(self) -> list[int]:
        fds: list[int] = []
        sfd = self._flow.fileno()
        if sfd is not None:
            fds.append(sfd)
        fds += list(self._voice_jobs)
        fds += list(self._cloud_jobs)           # ADR-056: in-flight off-loop cloud calls
        fds += list(self._recall_jobs)          # ADR-056/Gate 2: in-flight off-loop Stage-4 workers
        fds += list(self._file_jobs)            # on-device file eval: in-flight local summary -> chat
        fds += list(self._learn_jobs)           # v1.24.0 Sprint 3: in-flight off-loop Narsese distillation
        fds += list(self._triage_jobs)          # Slice 3a: in-flight off-loop deviation scan
        fds.append(self._localbrain.fileno())   # ADR-057: the Tier-2 decode worker's completion pipe
        fds += self._overnight.extra_fds()      # ADR-052: the offloaded summary worker's stdout
        if self._ingest_watch is not None and (ifd := self._ingest_watch.fileno()) is not None:
            fds.append(ifd)                     # v1.24.0: the FSEvents edge's flushed candidate batches
        return fds

    def handle_fd(self, fd: int) -> None:
        if fd in self._voice_jobs:
            self._read_voice(fd)
        elif fd in self._recall_jobs:            # ADR-056/Gate 2: drain a completed Stage-4 derivation
            self._read_recall(fd)
        elif fd in self._cloud_jobs:             # ADR-056: drain a completed cloud answer
            self._read_cloud(fd)
        elif fd in self._file_jobs:              # on-device file eval: drain a completed local summary
            self._read_file_job(fd)
        elif fd in self._learn_jobs:             # v1.24.0 Sprint 3: a distillation finished off-loop
            self._read_learn_job(fd)
        elif fd in self._triage_jobs:            # Slice 3a: a deviation scan finished off-loop
            self._read_triage_job(fd)
        elif fd == self._localbrain.fileno():    # ADR-057: a Tier-2 decode finished off-loop
            self._drain_converse()
        elif self._ingest_watch is not None and fd == self._ingest_watch.fileno():
            self._ingest_watch.read()            # v1.24.0: drain a flushed FSEvents candidate batch
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
        # ADR-057: the agent step and the persona idle-batch both call the local model. While a Tier-2
        # decode holds the LocalBrain, skip them — they'd otherwise block the loop on the context lock.
        if not self._localbrain.busy:
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
            self._drain_corpus()             # Slice 4: AC-gated serial bulk-ingest drain (cold-start baseline)
        except Exception as exc:  # noqa: BLE001 — a corpus-drain hiccup must never kill the loop
            self._emit("alert", {"text": f"[corpus error] {exc}"})
        try:
            idle = (time.time() - self._last_request_at) >= IDLE_SECONDS   # ADR-036: persona ingestion,
            self._persona_loop.tick(idle and not self._localbrain.busy,    # idle-gated, batched; ADR-057:
                                    overnight_active=self._overnight.active)  # never during a Tier-2 decode
        except Exception as exc:  # noqa: BLE001 — a persona hiccup must never kill the loop
            self._emit("alert", {"text": f"[persona error] {exc}"})
        try:
            # v1.24.0 Sprint 2: drain ONE captured candidate per tick, but only when the user is idle AND
            # the overnight runner is free (don't compete) AND no Tier-2 decode is in flight — so passive
            # ingestion stays an idle/overnight activity, never a tax on active work.
            if (self._ingest_drain is not None and idle and not self._overnight.active
                    and not self._localbrain.busy):
                self._ingest_drain.drain_once(on_ac=self._on_ac_power())
        except Exception as exc:  # noqa: BLE001 — a drain hiccup must never kill the loop
            self._emit("alert", {"text": f"[ingest error] {exc}"})
        try:
            self._sys_sentinel.run_once()
            import psutil
            self._last = f"cpu={psutil.cpu_percent():.0f}% mem={psutil.virtual_memory().percent:.0f}%"
        except Exception as exc:  # noqa: BLE001 — a flaky poll must never kill the loop
            self._emit("alert", {"text": f"[sentinel error] {exc}"})

    def close(self) -> None:
        for job in list(self._recall_jobs.values()):   # ADR-056/Gate 2: SIGKILL + reap any in-flight worker
            job.cleanup()
        self._recall_jobs.clear()
        for entry in list(self._file_jobs.values()):   # on-device file eval: reap any in-flight summarizer
            entry["job"].cleanup()
        self._file_jobs.clear()
        for entry in list(self._learn_jobs.values()):  # Sprint 3: reap any in-flight distillation worker
            entry["job"].cleanup()
        self._learn_jobs.clear()
        for entry in list(self._triage_jobs.values()): # Slice 3a/4: reap any in-flight deviation scan
            entry["job"].cleanup()
        self._triage_jobs.clear()
        self._paramstore.close()          # Slice 4: the corpus dedup/size read handle
        self._localbrain.close()          # ADR-057: close the Tier-2 decode self-pipe
        if self._ingest_watch is not None:
            self._ingest_watch.stop()     # v1.24.0: reap the FSEvents helper + close the ingest queue
        self._flow.close()
        self._brain.close()
        self._lexicon.close()             # ADR-056/Gate 2: the L2 namespace index
        self._recall_metrics.close()      # ADR-056 §8: compounding telemetry
        self._summaries.close()           # ADR-058: the Summary archive (CodeRabbit PR#1: was leaking its SQLite conn)
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
