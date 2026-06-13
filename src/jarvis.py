"""NARS-JARVIS application orchestrator — composes brain (L1) + language + memory (L2).

The M0 capability-C1 loop: `learn(english)` writes through to L2 and feeds L1; `ask(question)`
answers from L1 with an evidence trail, and on a cache miss reloads from L2 and retries.
Composes domains via their public interfaces only (ADR-001). Imperative Shell (S-02).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, replace
from typing import Callable

from actions import drop_nominal_verdict, render_action_prompt
from actions import resolve as _resolve_action
from brain import Brain, canonical_input, input_accepted
from context import (
    ConversationBuffer,
    conflicting_habit,
    correction_notice,
    ground_answer,
    grounding_notice,
    is_volatile,
)
from contradiction import ContradictionGuard
from execution import DecisionStats, Executor, decide
from language import (
    QUESTION_SYSTEM_PROMPT,
    Decision,
    IngestionGate,
    Polarity,
    Translator,
    Verdict,
    Voice,
    assess,
    back_render,
    filter_known,
    filter_semantic,
    memory_acknowledgment,
    split_do_directives,
    split_forget_directives,
    split_memory_directives,
    to_narsese,
)
from memory import (
    MemoryStore,
    MetricsStore,
    is_valid_belief,
    observe,
    reload_into_brain,
    same_single_valued_slot,
    slot_of,
    statement_term,
    statement_truth,
)
from research import run_research


class InvalidNarseseError(ValueError):
    """A `tell` statement is not a well-formed Narsese belief, or ONA rejected it on parse."""


@dataclass(frozen=True)
class GateItem:
    """One claim's gate outcome, in UX-renderable form (decoupled from console I/O)."""
    english_mirror: str          # the canonical English round-trip (back_render)
    reason: str
    cosine: float | None
    statement: str               # the compiled Narsese (for reference)


# Console-injected I/O hooks (the core never does terminal I/O itself):
RejectPresenter = Callable[[list[GateItem]], None]      # Phase 1: show what bounced
EscalationConfirm = Callable[[GateItem], bool]          # Phase 2: ask the human [y/n]


def _reject_outcome(res) -> tuple[str | None, str]:
    """Map a gate REJECT to a privacy-safe telemetry (layer, outcome) — no text."""
    if res.layer == "L0":
        return ("L0", "REJECT_FUSED" if "fused" in res.reason else "REJECT_STRUCTURAL")
    return ("L1", "REJECT_SEMANTIC")


# The prompt inversion (ADR-007): the LLM is the brain, ONA-backed memory is ground truth.
# Auto-memory (ADR-008): the assistant marks things worth remembering with a directive whose
# syntax is owned by language.extract.REMEMBER_TAG — keep these two in lockstep.
ASSISTANT_SYSTEM_PROMPT = (
    "You are JARVIS, a capable, concise local AI assistant running fully offline on the user's Mac. "
    "Answer the user's request directly, using your own knowledge to reason, explain, and write code. "
    "A 'Persistent memory' section may be provided with facts the user has taught you and their "
    "preferences — treat those as absolute ground truth and prefer them over your own knowledge when "
    "they are relevant. If you are unsure, say so briefly rather than inventing specifics. Be direct.\n\n"
    "NO INTERNET / NO LIVE DATA — this is critical. You run fully offline with no web access. You do "
    "NOT know current events, live sports scores or game schedules, today's news, real-time prices, "
    "weather, flight status, or anything that changes after your training cutoff. If asked for any such "
    "live or external fact, you MUST NOT guess, invent, or state a specific answer (no made-up times, "
    "scores, or numbers). Instead say exactly: \"I can't answer that on-device — I have no internet or "
    "live data. Toggle the cloud (☁️) and I'll look it up.\" The ONLY real-time facts you have are "
    "the date/time and system info in the 'Current context' section; everything else external is unknown "
    "to you, and a confident wrong answer is far worse than admitting you can't know.\n\n"
    "MEMORY PROTOCOL — follow exactly. Whenever the user states a durable fact about themselves "
    "(name, role, where they live), a lasting preference, or explicitly asks you to remember "
    "something, you MUST end your reply with one tag per item, each on its own line, written "
    "literally as: [[REMEMBER: <concise third-person fact>]]. Do this even while also answering a "
    "question in the same message. For ordinary questions, greetings, or small talk, do NOT add any "
    "tag. Never mention or explain the tag — it is stripped before the user sees it.\n"
    "CRITICAL: tag ONLY genuinely NEW information from the user's current message. NEVER emit a tag "
    "for anything already listed in the 'Persistent memory' section — you already know it.\n"
    "Only emit [[FORGET: <old fact>]] when the user EXPLICITLY retracts a fact ('forget that…', "
    "'I no longer…', 'that's wrong') or directly changes a single-valued fact (a new name/location). "
    "Do NOT forget a still-true fact just because the user adds a new COMPATIBLE one — liking coffee "
    "does not mean they stopped liking tea.\n"
    "LIVE CONTEXT: a 'Current context' section gives the real date/time, system load, and (if known) "
    "the user's foreground activity. Answer time/date/'what am I doing' questions FROM it. Never "
    "[[REMEMBER]] anything from it — it is ephemeral, not a durable fact.\n"
    "LEARNED HABITS: a 'Learned habits' section states standing preferences about how JARVIS may act "
    "(what it may or may NOT auto-do). Treat them as firm boundaries. If the user asks you to do "
    "something a habit says NOT to do, do NOT agree — remind them they've disabled it and that they "
    "can re-enable it by approving it. Never [[REMEMBER]] a habit; it is managed separately.\n"
    "Worked examples (note the tag lines):\n"
    "User: my name is Ashkan\n"
    "Assistant: Nice to meet you, Ashkan!\n"
    "[[REMEMBER: the user's name is Ashkan]]\n"
    "User: I'm a pilot and I prefer tabs over spaces\n"
    "Assistant: Good to know.\n"
    "[[REMEMBER: the user is a pilot]]\n"
    "[[REMEMBER: the user prefers tabs over spaces]]\n"
    "User: forget that I like tea\n"
    "Assistant: Done.\n"
    "[[FORGET: the user likes tea]]\n"
    "User: what is the capital of France?\n"
    "Assistant: Paris."
)


# Bounded agent loop (ADR-024 Phase 2): the focused re-prompt after navigation. The model is shown
# ONLY the goal + the current on-screen controls and must emit exactly one directive.
AGENT_STEP_PROMPT = (
    "You are operating the Mac's GUI to fulfill the user's goal. You are shown the controls currently "
    "on screen. Emit EXACTLY ONE directive and nothing else:\n"
    "- [[DO: ax_press: <id>]] to click the control that fulfills the goal,\n"
    "- [[DO: ax_set_value: <id> <value>]] to set a slider/field,\n"
    "- [[DO: ax_set_checked: <id> 1]] (or 0) to turn a checkbox/toggle on (or off),\n"
    "- [[DO: navigate: <app or settings pane>]] if the needed control is NOT in the list,\n"
    "- or reply 'cannot' if it isn't possible. Use an id from the list verbatim; never invent one."
)

# v1.8.2: words that indicate the user is actually asking about the computer's system/performance.
# Used to gate report_system in code (the 7B over-proposes it as a generic "let me check"). Tuned to
# match the real system asks ("what's my CPU", "is anything wrong with my mac", "system report") while
# NOT matching ordinary questions (sunrise, weather, chit-chat).
# ADR-040 tightening: the bare device nouns (computer/machine/mac/laptop/system) are GONE from the
# keyword alternation — "why doesn't the volume button on my computer work" matched on "computer"
# alone and let a CPU/memory report masquerade as an audio answer. Device nouns now count only inside
# explicit health phrasings ("how's my mac", "is my computer ok", "check my mac", "system report").
_SYSTEM_QUERY = re.compile(
    r"\b(cpu|memory|ram|disk|storage|battery|power|performance|perf|slow|laggy|lag|"
    r"freez\w*|temperature|thermal|overheat\w*|fans?|resources?|utiliz\w*|diagnostics?)\b"
    r"|\bsystem\s+(report|status|health|check)\b"
    r"|\b(anything|something|what'?s)\s+wrong\b"
    r"|\brunning\s+(hot|slow|fine|ok)\b"
    r"|\bhow'?s?\s+(my|the)\s+(mac|computer|machine|system)\b"
    r"|\bis\s+(my|the)\s+(mac|computer|machine|laptop|system)\s+(ok|okay|fine|alright|healthy)\b"
    r"|\bcheck\s+(on\s+)?(my|the)\s+(mac|computer|machine|laptop|system)\b",
    re.I,
)

# ADR-045: the NARROWER subset of system questions that are actually about HEALTH ("is something
# wrong / slow / hot / ok"). report_system still fires for any _SYSTEM_QUERY (a memory/CPU data
# question needs the data), but the unsolicited "Nothing looks wrong" verdict is dropped unless the
# user asked a health question — "which app uses the most memory" is data, not "is anything wrong".
# A real anomaly is ALWAYS surfaced regardless (a problem is never unsolicited).
_HEALTH_QUERY = re.compile(
    r"\b(wrong|ok|okay|fine|alright|healthy|broken|slow|laggy|lag|freez\w*|hot|overheat\w*|"
    r"crash\w*|problem|issue|trouble|diagnos\w*|health)\b"
    r"|\bsystem\s+(report|status|health|check)\b"
    r"|\brunning\s+(hot|slow|fine|ok)\b", re.I)

# ADR-042: words showing the user actually wants a BROWSER opened (tab/window) — the only case where
# the model's web_search (argv tab-opener, returns nothing) choice is honored; otherwise a web_search
# directive is rerouted to web_lookup (the research loop), because the intent was to gather facts.
_BROWSER_INTENT = re.compile(r"\b(open|browser|tab|chrome|safari|firefox)\b", re.I)

# ADR-044: words showing the user actually wants to MANIPULATE an on-screen control. The focused
# window's accessibility controls are injected into converse with "you may act on these", and a small
# 7B answered a plain chat turn ("is your name actually Jarvis?") and ALSO tacked on a spurious
# [[DO: ax_press: button_23]] — the consent gate then queued a random click. This gate decides, in
# code, whether the AX controls are shown at all AND whether any ax_* directive is honored. Strong,
# rarely-conversational tokens only (conservative: a missed "select the option" is a smaller failure
# than a phantom click; the user just rephrases). NOT the audio "volume button" case — that routes to
# audio_status (ADR-040), never to actuation.
_UI_ACTION_INTENT = re.compile(
    r"\b(click|clicks|clicked|press|presses|pressed|tap|toggle|toggled|untoggle|"
    r"uncheck|tick|checkbox|check\s?box|slider|sliders|button|buttons|radio button|"
    r"drag|drop\s?down|dropdown|menu item|scroll\s?bar)\b"
    r"|\bset\s+\S+\s+to\b"                                    # "set brightness to 45%", "set it to 50"
    r"|\b(select|choose|enable|disable)\s+(the|that|this|it)\b", re.I)

# ADR-040: the matching intent gate for the audio sensor — audio_status runs only when the user is
# actually asking about sound/volume, the same proposal/disposal split as _SYSTEM_QUERY above.
_AUDIO_QUERY = re.compile(
    r"\b(volume|sound|audio|mute[d]?|unmute[d]?|speakers?|headphones?|silent|quiet|loud\w*|"
    r"hear\w*|music)\b",
    re.I,
)

# ADR-046: the intent gate for the network-inspection sensor. network_status runs only when the user
# asks about the internet/network/Wi-Fi — so the 7B can't grab it as a generic "let me check", and so
# a network question stops falling back to generic web research (the gap that motivated this sensor).
_NETWORK_QUERY = re.compile(
    r"\b(internet|wi-?fi|wlan|bandwidth|ethernet|network|connectivity|latency|"
    r"download speed|upload speed|router|hotspot)\b"
    r"|\b(slow|fast|speed|laggy|dropping).{0,20}\b(internet|connection|network|online|download|stream)\b"
    r"|\b(connection|online).{0,20}\b(slow|fast|down|dropping|laggy)\b", re.I)

# ADR-047: the intent gate for the installed-apps disk sensor — largest_apps fires only when the user
# asks about app sizes / disk space, so "what's the largest app" stops falling through to find_file.
_APPS_QUERY = re.compile(
    r"\b(large|larg\w*|big|bigg\w*|huge|heav\w*)\b.{0,20}\b(app|apps|application|applications|"
    r"program|programs|software)\b"
    r"|\b(app|apps|application|applications|program|programs|software)\b.{0,30}"
    r"\b(install|size|large|big|space|storage|disk|gb|mb|taking)\b"
    r"|\binstalled (app|apps|application|applications|program|software)\b"
    r"|\b(disk space|storage space|taking up.{0,15}space)\b", re.I)

# ADR-035/039: web actions that trigger the bounded research loop (research/) instead of dumping raw
# results into the chat — the loop searches, opens the links the model judges relevant, and synthesizes.
_RESEARCH_ACTIONS = ("web_lookup", "read_article")


class Jarvis:
    def __init__(self, translator: Translator, store: MemoryStore, brain: Brain,
                 guard: ContradictionGuard | None = None,
                 executor: Executor | None = None,
                 gate: IngestionGate | None = None,
                 metrics: MetricsStore | None = None,
                 voice: Voice | None = None,
                 assistant: object | None = None,
                 embedder: object | None = None,
                 context_provider: Callable[[], str] | None = None,
                 habits_provider: Callable[[], str] | None = None,
                 persona_provider: Callable[[], str] | None = None,
                 sentinel_beliefs_provider: Callable[[], list[tuple[str, float, float]]] | None = None,
                 action_runner: object | None = None,
                 consent_opener: Callable[[str, Callable[[], object]], object] | None = None,
                 ax_provider: Callable[[], str] | None = None,
                 ax_dispatch: Callable[[str, str], str] | None = None,
                 nav_dispatch: Callable[[str, str], str] | None = None,
                 navigate: Callable[[str, str], str] | None = None,
                 habit_observer: Callable[[str, str, str], None] | None = None,
                 habit_admin: Callable[[str, str], str] | None = None,
                 lexicon_sink: Callable[[str], None] | None = None,
                 ) -> None:
        self._translator = translator
        self._store = store
        self._brain = brain
        self._guard = guard
        self._executor = executor  # None => orchestrator stays learn/ask only (no execution path)
        self._gate = gate          # None => ungated learn (no semantic gate; e.g. no embedder)
        self._metrics = metrics    # None => no telemetry; gate-friction outcomes only, never text
        # Embedder for the auto-memory semantic echo-guard (ADR-008). None => guard degrades to the
        # verbatim/normalized filter + prompt only (tests / offline).
        self._embedder = embedder if (embedder is not None and hasattr(embedder, "embed")) else None
        # ADR-056 / Gate 2: best-effort hook fired with each committed canonical Narsese term, so the L2
        # lexicon populates as a living reflection of ingestion. None => no lexicon (tests/offline).
        self._lexicon_sink = lexicon_sink
        # Dynamic context (ADR-010): a shell-provided callable returning the fresh live-facts block
        # (date/time + system + foreground) injected each turn. None => no live context (tests/offline).
        self._context_provider = context_provider
        # Learned habits (ADR-012): a callable returning the translated "Learned habits" block from the
        # sentinel's persisted beliefs. None => no habits block (tests/offline).
        self._habits_provider = habits_provider
        self._persona_provider = persona_provider   # ADR-036: [COGNITIVE CONTEXT CONSTRAINTS] prefix
        # Pre-commit grounding (ADR-013): a callable returning the raw persisted sentinel beliefs, so
        # converse can drop conversational memories that try to control a sentinel-governed category.
        self._sentinel_beliefs_provider = sentinel_beliefs_provider
        # Conversational Mac actions (ADR-019): a runner exposing available()/perform(name, arg). The
        # LLM proposes a [[DO:]] action; the runner's CLOSED catalog validates + executes it. None =>
        # no action surface (tests/offline) and no ACTIONS section in the prompt.
        self._action_runner = (action_runner
                               if (action_runner is not None and hasattr(action_runner, "perform"))
                               else None)
        # Interactive consent (ADR-020): a callable (label, on_approve) -> id that opens a consent
        # request for a destructive action and runs on_approve only on the human's approval. None =>
        # destructive actions are safely refused (tests/offline), never run unconfirmed.
        self._consent_opener = consent_opener
        # GUI actuation (ADR-021): ax_provider injects the focused window's accessibility DOM into the
        # prompt; ax_dispatch(verb, arg) validates+consent-gates a [[DO: ax_*]] verb and emits the
        # actuate event to the app. None => no GUI control surface (tests/offline; non-app clients).
        self._ax_provider = ax_provider
        self._ax_dispatch = ax_dispatch
        # Navigation recipes (ADR-022): high-level verbs (e.g. set_brightness) where the daemon opens
        # the right surface itself and actuates — works regardless of what's focused. None => unavailable.
        self._nav_dispatch = nav_dispatch
        # Bounded agent loop (ADR-024 Phase 2): a callable (target, question) -> str that opens a surface
        # and arms the navigate→re-perceive→act loop in the daemon. None => no agent loop (tests/offline).
        self._navigate_cb = navigate
        # Habit Brain (ADR-026): a callable (action, arg, outcome) that records an executed action as NARS
        # evidence so recurring patterns become proposable habits. None => no habit learning (tests).
        self._habit_observer = habit_observer
        # Habit introspection/pruning (ADR-027): (verb, arg) -> finished text (list_habits/forget_habit).
        self._habit_admin = habit_admin
        self._voice = voice or Voice()  # template-only by default; formatter LLM is optional
        # LLM-first brain (ADR-007): when a real model is wired, converse() lets the LLM answer from
        # its own knowledge with the user's persistent memory injected as ground truth. With no
        # assistant (tests / no model) converse() falls back to the legacy ONA-grounded path.
        self._assistant = assistant if (assistant is not None and hasattr(assistant, "generate_text")) else None
        # ADR-041: the sliding short-term conversation window (in-memory, session-bounded) that makes
        # follow-up questions work. Render-only — it never feeds memory/persona/habit pipelines.
        self._chat = ConversationBuffer()

    def learn(self, sentence: str, *, on_rejects: RejectPresenter | None = None,
              confirm_escalation: EscalationConfirm | None = None) -> list[str]:
        """English -> committed Narsese, via the batch-and-queue ingestion gate (transactional UX).

        With a gate wired, EVERY extracted claim is evaluated FIRST (no commit, no output), then:
          Phase 1 — present the rejects (educational mirror) via `on_rejects`;
          Phase 2 — resolve escalations sequentially via `confirm_escalation` ([y/n]);
          Phase 3 — commit the final set through ONA's canonical check to L2.
        Returns the committed statements (the caller prints the single clean summary). With no gate,
        falls back to the ungated write-through (used where there is no embedder). Pure of terminal
        I/O — all human interaction is via the injected hooks.
        """
        if self._gate is None:
            return self._learn_ungated(sentence)
        try:
            claims = self._translator.claims(sentence)
        except Exception:  # noqa: BLE001 — malformed model output must not crash the REPL
            return []
        commit_q: list[object] = []
        reject_q: list[GateItem] = []
        escalate_q: list[tuple[object, GateItem]] = []
        metric_rows: list[tuple[str | None, str]] = []        # (layer, outcome) — never any text
        for claim in claims:  # evaluate ALL before any commit/output (batch)
            res = self._gate.evaluate(claim, sentence)
            item = GateItem(res.back_render or back_render(claim), res.reason, res.cosine,
                            to_narsese(claim))
            if res.decision is Decision.COMMIT:
                commit_q.append(claim)
                metric_rows.append((res.layer, "COMMIT_CLEAN"))
            elif res.decision is Decision.REJECT:
                reject_q.append(item)
                metric_rows.append(_reject_outcome(res))
            else:  # ESCALATE
                escalate_q.append((claim, item))
        if reject_q and on_rejects is not None:               # Phase 1
            on_rejects(reject_q)
        for claim, item in escalate_q:                        # Phase 2
            if confirm_escalation is not None and confirm_escalation(item):
                commit_q.append(claim)
                metric_rows.append(("L1", "ESCALATE_ACCEPTED"))
            else:
                metric_rows.append(("L1", "ESCALATE_DECLINED"))
        committed: list[str] = []                             # Phase 3
        output: list[str] = []
        for claim in commit_q:
            s = self._commit(to_narsese(claim), sentence, output)
            if s is not None:
                committed.append(s)
        observe(self._store, output)
        if self._metrics is not None:
            self._metrics.record_batch(metric_rows)           # fire-and-forget telemetry
        return committed

    def commit_approved(self, statement: str, sentence: str) -> str | None:
        """Commit a claim the human explicitly approved (an accepted L1 escalation). Bypasses the
        gate — the human is the arbiter (L2 design) — but still goes through the C2 guard, ONA's
        canonical check, and the L2 write-through. Returns the committed statement, or None.
        """
        output: list[str] = []
        s = self._commit(statement, sentence, output)
        observe(self._store, output)
        return s

    def _learn_ungated(self, sentence: str) -> list[str]:
        """Original write-through (no semantic gate): translate -> commit each statement."""
        result = self._translator.translate(sentence)
        if not result.ok:
            return []
        committed: list[str] = []
        output: list[str] = []
        for statement in result.narsese:
            s = self._commit(statement, sentence, output)
            if s is not None:
                committed.append(s)
        observe(self._store, output)
        return committed

    def _commit(self, statement: str, sentence: str, output: list[str]) -> str | None:
        """C2 guard -> feed L1 -> confirm ONA accepted -> canonical write-through to L2.
        Returns the committed statement, or None if deferred (contradiction) or ONA-rejected.
        """
        if self._guard is not None and self._guard.check(statement) is not None:
            return None  # contradiction flagged to human; defer, keep L1/L2 protected
        out = self._brain.add_belief(statement)  # feed L1 FIRST
        output += out
        if not input_accepted(out):
            return None  # ONA rejected on parse; do not commit (keeps L1/L2 in sync)
        term, frequency, confidence = self._canonical(out, statement)
        self._store.upsert(term, frequency, confidence, english=sentence)  # canonical, after L1 OK
        self._fire_lexicon(term)
        return statement

    def _fire_lexicon(self, term: str) -> None:
        """Feed the committed canonical term to the L2 lexicon. Best-effort: a lexicon error must NEVER
        break ingestion (the system of record is L1/L2; the lexicon is a derived index)."""
        if self._lexicon_sink is not None:
            try:
                self._lexicon_sink(term)
            except Exception:  # noqa: BLE001
                pass

    def _canonical(self, output: list[str], statement: str) -> tuple[str, float, float]:
        """ONA's normalized (term, freq, conf) from the 'Input:' echo, so L2 mirrors L1 exactly.
        Falls back to the raw statement's term/truth only if the echo is unexpectedly absent.
        """
        echo = canonical_input(output)
        if echo is not None and echo.term:
            if echo.truth is not None:
                return echo.term, echo.truth.frequency, echo.truth.confidence
            return echo.term, *statement_truth(statement)
        return statement_term(statement), *statement_truth(statement)

    def tell(self, statement: str) -> bool:
        """Ingest a raw Narsese belief directly (no LLM), durable like `learn` but desync-proof.

        Ingress order matters: (1) reject malformed syntax BEFORE any side effect; (2) C2 guard;
        (3) feed L1 and CONFIRM ONA accepted it (echoed 'Input:', no parse error); (4) only THEN
        write through to L2 (english=""). So a parse-rejected string never reaches the L2 system of
        record, and L1/L2 cannot desync. Returns True if committed, False if deferred by the guard.
        Raises InvalidNarseseError on malformed syntax or an ONA parse rejection.
        """
        statement = statement.strip()
        if not is_valid_belief(statement):
            raise InvalidNarseseError(
                f"not a well-formed Narsese belief: {statement!r} "
                "(expected a term + '.'  e.g.  <a --> b>.  or  <cpu --> [pegged]>. {0.0 0.9})"
            )
        if self._guard is not None and self._guard.check(statement) is not None:
            return False  # contradiction flagged to human; defer commit, keep L1/L2 protected
        output = self._brain.add_belief(statement)  # feed L1 FIRST
        if not input_accepted(output):
            raise InvalidNarseseError(f"ONA rejected the statement on parse; not committed: {statement!r}")
        # Store ONA's NORMALIZED form (same canonical capture as learn), not the raw typed string,
        # so the L2 system of record is a pristine reflection of L1 ('< A --> B > .' -> '<A --> B>').
        term, frequency, confidence = self._canonical(output, statement)
        self._store.upsert(term, frequency, confidence, english="")  # commit L2 only after L1 OK
        observe(self._store, output)  # persist any truths ONA revised/derived this step
        self._fire_lexicon(term)
        return True

    def ask(self, question: str):
        """Answer from L1; on a cache miss, repopulate L1 from L2 and retry once."""
        answer = self._brain.ask(question)
        if answer is None:
            reload_into_brain(self._store, self._brain)
            answer = self._brain.ask(question)
        return answer

    def converse(self, question: str) -> str:
        """Synchronous LLM-first answer (ADR-007). Kept for voice and offline tests; the daemon's Tier-2
        path instead drives the same three stages OFF the loop — `converse_begin` (prompt assembly) →
        async decode → `converse_resume` (post-processing) — so a 512-token decode never blocks select()
        (ADR-057). Both paths record the turn exactly once, on every return path (ADR-041)."""
        state = self.converse_begin(question)
        if state is None:
            return self.converse_fallback(question)
        try:
            reply = self._assistant.generate_text(state["system"], state["user"], max_tokens=512)
        except Exception:  # noqa: BLE001 — a model hiccup degrades to the grounded path, never crashes
            return self.converse_fallback(question)
        return self.converse_resume(state, reply)

    def clear_conversation(self) -> None:
        """ADR-041: explicitly end the short-term conversation window (durable memory untouched)."""
        self._chat.clear()

    def converse_fallback(self, question: str) -> str:
        """The legacy ONA-grounded answer, used when no language model is wired or a decode fails. Records
        the turn (ADR-041), like every converse return path."""
        reply = self._converse_grounded(question)
        self._chat.observe(question, reply)
        return reply

    def converse_begin(self, question: str) -> dict | None:
        """Stage 1 (ADR-057): assemble the prompt from persistent memory + live context + the turn window.
        Touches ONA/SQLite, so it runs on the main thread. Returns the decode inputs and the context the
        post-processing needs; `None` signals no model wired → the caller should use `converse_fallback`."""
        if self._assistant is None:
            return None
        memory = self._recall(question)
        live = self._context_provider() if self._context_provider is not None else ""
        habits = self._habits_provider() if self._habits_provider is not None else ""
        # ADR-044: only show the focused-window controls when the user actually asked to act on a
        # control — otherwise the "you may act on these" block provokes spurious clicks on chat turns
        # (and wastes prefill on every turn). Disposal is also gated below as the firewall.
        ax = (self._ax_provider() if (self._ax_provider is not None
                                      and self._is_ui_action_request(question)) else "")
        blocks: list[str] = []
        if live:
            blocks.append(live)
        if habits:                                            # ADR-012: learned preferences to respect
            blocks.append(habits)
        if ax:                                                # ADR-021: on-screen controls to act on
            blocks.append(ax)
        if memory:
            blocks.append("Persistent memory (the user taught you these; treat as ground truth):\n"
                          + memory)
        if (chat := self._chat.render()):                     # ADR-041: the sliding turn window —
            blocks.append(chat)                               # adjacent to the question, transcript-style
        blocks.append(f"User: {question}")
        user = "\n\n".join(blocks)
        # Actions (ADR-019): when a runner is wired, teach the LLM the closed action set so it can
        # request one with [[DO:]]. Appended to the base prompt so the rest of the protocol is intact.
        system = ASSISTANT_SYSTEM_PROMPT
        if self._action_runner is not None:
            system = system + "\n\n" + render_action_prompt(self._action_runner.available())
        # ADR-036: prepend the learned persona constraints (style/focus) to the system prompt. Read from
        # SQLite (O(1), no ONA on the hot path); '' when nothing is confident enough or the layer is down.
        if self._persona_provider is not None and (persona := self._persona_provider()):
            system = system + "\n\n" + persona
        return {"question": question, "system": system, "user": user,
                "memory": memory, "live": live, "habits": habits, "chat": chat}

    def converse_resume(self, state: dict, reply: str) -> str:
        """Stage 3 (ADR-057): turn the model's raw `reply` into the final answer — execute [[DO:]] actions,
        resolve [[REMEMBER]]/[[FORGET]], ground against held self-facts. Touches ONA/SQLite, so it runs on
        the main thread once the off-loop decode returns. Records the turn (ADR-041) on every path."""
        final = self._resume_inner(state, reply)
        self._chat.observe(state["question"], final)
        return final

    def _resume_inner(self, state: dict, reply: str) -> str:
        question, memory, live, habits, chat = (state["question"], state["memory"],
                                                state["live"], state["habits"], state["chat"])
        # Actions (ADR-019): pull [[DO:]] directives and execute each via the runner's closed catalog
        # (an unknown/unsafe action is a safe no-op string). Done first so the tags are stripped before
        # the memory parsers run; results are appended to the reply below.
        clean, actions = split_do_directives(reply)
        # Auto-memory (ADR-008/009): pull [[REMEMBER]]/[[FORGET]] from the model's OWN prose FIRST — a
        # research synthesis pass (below) may replace that prose with the web-sourced answer.
        clean, facts = split_memory_directives(clean)
        clean, forgets = split_forget_directives(clean)
        clean = clean.strip()
        # Deterministic tool-choice reroute (ADR-042): the 7B sometimes grabs web_search (the
        # browser-tab opener that RETURNS NOTHING) to gather facts, despite the prompt forbidding it —
        # observed live on "how is the weather tomorrow" (a Google tab opened; no research ran). If the
        # user's text doesn't actually ask for a browser, the intent was research: reroute to
        # web_lookup. Code disposes; an explicit "open a search in my browser" still gets the tab.
        actions = [("web_lookup" if (n == "web_search" and not _BROWSER_INTENT.search(question))
                    else n, a) for n, a in actions]
        # Actions: run normal ones (results appended as a tail below). Research actions (web_lookup /
        # read_article, ADR-035) instead get a SECOND model pass that synthesizes the answer FROM the
        # findings — so the user sees a real answer, not raw search results dumped into the chat.
        research = [t for t in actions if t[0] in _RESEARCH_ACTIONS]
        action_results = self._run_actions([t for t in actions if t[0] not in _RESEARCH_ACTIONS], question)
        if research:
            answer, errors = self._run_research(research, question, chat)
            if answer:
                clean = answer            # the synthesized, web-sourced answer replaces the "Let me check" filler
            action_results += errors      # surface any [ERROR…] (rate-limited/blocked) honestly
        if facts:  # guard the context-echo bug — but an UPDATE (same slot, new value) is not an echo
            known = [ln[2:] if ln.startswith("- ") else ln
                     for ln in (memory + "\n" + live + "\n" + habits).splitlines()]  # incl. live + habits
            # A same-single-valued-slot-but-different-value fact is a correction that must reach the
            # store to supersede the old value; only NON-updates run through the echo guards.
            updates = [f for f in facts if any(same_single_valued_slot(f, k) for k in known)]
            others = [f for f in facts if f not in updates]
            others = filter_known(others, known)               # verbatim / normalized echoes
            if others and self._embedder is not None:
                try:
                    others = filter_semantic(others, known, self._embedder.embed)  # paraphrase echoes
                except Exception:  # noqa: BLE001 — an embed hiccup must not crash converse
                    pass
            facts = updates + others
        # Pre-commit grounding (ADR-013): a fact that tries to control a sentinel-governed category is
        # a control-plane statement — it belongs in the gate, not conversational memory. Drop it; the
        # deterministic layer will own the reply (the LLM's possibly-agreeing prose is suppressed).
        grounded: tuple[str, bool] | None = None
        if facts and self._sentinel_beliefs_provider is not None:
            beliefs = self._sentinel_beliefs_provider()
            kept: list[str] = []
            for f in facts:
                hit = conflicting_habit(f, beliefs)
                if hit is not None and grounded is None:
                    grounded = hit
                elif hit is None:
                    kept.append(f)
            facts = kept
        # Resolve memory ops BEFORE deciding on a fallback, so a directive-only reply (the 7B sometimes
        # emits just the tag, no prose) is never discarded.
        saved = self._remember_facts(facts, source=question) if facts else []
        forgotten = self._forget_facts(forgets) if forgets else []
        if grounded is not None:  # control-plane conflict: deterministic layer OWNS the reply (ADR-013)
            return grounding_notice(*grounded)
        # Output grounding (ADR-014): if the answer flagrantly contradicts a held single-valued
        # self-fact, suppress the hallucination and return a visible correction. Relevance-gated:
        # only runs when we actually hold self-facts (pure regex, no model, no ONA on the hot path).
        if clean:
            hit = ground_answer(clean, self._held_self_facts())
            if hit is not None:
                return correction_notice(hit[0], hit[1])
        acks = [a for a in (memory_acknowledgment(saved),
                            ("(Forgot: " + "; ".join(forgotten) + ")") if forgotten else "") if a]
        # Tail = action results (e.g. the system report or "(Done: …)") then the save/forget ack line.
        tail = action_results + ([" ".join(acks)] if acks else [])
        if not clean:  # no prose: show the action results / confirmation, else fall back to grounded
            return "\n".join(tail) if tail else self._converse_grounded(question)
        return "\n".join([clean, *tail]) if tail else clean

    def _record_habit(self, action: str, arg: str) -> None:
        """Feed an executed action to the Habit Brain (ADR-026); the observer ignores non-eligible
        (read-only/destructive) actions, so only safe repeatable state-changers form habits."""
        if self._habit_observer is not None:
            try:
                self._habit_observer(action, arg, "did")
            except Exception:  # noqa: BLE001 — habit telemetry must never break the turn
                pass

    def agent_step(self, request: str) -> list[tuple[str, str]]:
        """One bounded-agent-loop step (ADR-024 Phase 2): show the model the goal + the current
        on-screen controls and PARSE (not execute) its single directive. The daemon routes the result
        (navigate again / consent-gated act / give up). Returns [] when no model or no controls."""
        if self._assistant is None:
            return []
        dom = self._ax_provider() if self._ax_provider is not None else ""
        if not dom:
            return []
        try:
            reply = self._assistant.generate_text(AGENT_STEP_PROMPT, f"Goal: {request}\n\n{dom}",
                                                  max_tokens=96)
        except Exception:  # noqa: BLE001 — a model hiccup ends the loop safely (no action)
            return []
        _clean, actions = split_do_directives(reply)
        return actions

    def _run_research(self, research: list[tuple[str, str]], question: str,
                      chat_context: str = "") -> tuple[str | None, list[str]]:
        """ADR-039/042 agentic web answer: hand the model's research directives to the bounded
        link-following loop (research/), which searches, opens the pages the model picks (floor: at
        least one when links exist), and synthesizes an answer with sources. `chat_context` is the
        recent-conversation block so follow-up questions research what they refer to. The loop logs
        its trajectory to stderr (the daemon log). Never raises."""
        if self._action_runner is None:
            return None, []
        generate = lambda system, user, max_tokens: self._assistant.generate_text(
            system, user, max_tokens=max_tokens)
        log = lambda m: print(f"[research] {m}", file=sys.stderr, flush=True)
        return run_research(question, research, generate, self._action_runner.perform,
                            context=chat_context, log=log)

    @staticmethod
    def _is_system_query(text: str) -> bool:
        """True iff the user's text actually asks about the computer's system/performance. The
        deterministic guard for report_system (v1.8.2): the 7B uses it as a generic "let me check"
        escape hatch and fired it on unrelated questions (e.g. tomorrow's sunrise). Code, not the
        prompt, decides whether a system report is warranted."""
        return bool(_SYSTEM_QUERY.search(text or ""))

    @staticmethod
    def _is_audio_query(text: str) -> bool:
        """True iff the user's text is actually about sound/volume — the matching gate for the
        audio_status sensor (ADR-040), so it can never become the 7B's next generic escape hatch."""
        return bool(_AUDIO_QUERY.search(text or ""))

    @staticmethod
    def _is_network_query(text: str) -> bool:
        """True iff the user is asking about the internet/network/Wi-Fi — the gate for network_status
        (ADR-046), so a network question gets the local sensor instead of generic web research."""
        return bool(_NETWORK_QUERY.search(text or ""))

    @staticmethod
    def _is_apps_query(text: str) -> bool:
        """True iff the user is asking about installed-app sizes / disk space — the gate for
        largest_apps (ADR-047), so "what's the largest app" doesn't fall through to find_file."""
        return bool(_APPS_QUERY.search(text or ""))

    @staticmethod
    def _is_health_query(text: str) -> bool:
        """True iff the user asked about system HEALTH (something wrong/slow/ok), not just a system
        DATA point. Gates the report's reassurance verdict (ADR-045), never the report itself."""
        return bool(_HEALTH_QUERY.search(text or ""))

    @staticmethod
    def _is_ui_action_request(text: str) -> bool:
        """True iff the user's text asks to manipulate an on-screen control — the gate (ADR-044) for
        BOTH showing the focused-window AX controls in the prompt AND honoring any ax_* directive, so a
        plain chat turn can never produce a phantom click no matter what the 7B appends."""
        return bool(_UI_ACTION_INTENT.search(text or ""))

    def _run_actions(self, actions: list[tuple[str, str]], question: str = "") -> list[str]:
        """Execute parsed [[DO:]] directives through the wired runner; return each result string. A
        reversible action runs immediately; a destructive (`confirm`) action is routed through the
        consent gate (ADR-020) and only acknowledged here — it runs on approval. The closed catalog is
        the safety boundary: unknown/unsafe actions return a refusal and never spawn. No runner
        (tests/offline) => no actions performed; no consent channel => destructive actions refused."""
        if not actions:
            return []
        results: list[str] = []
        for name, arg in actions:
            if name == "navigate":                             # ADR-024 P2: open a surface, arm the loop
                if self._navigate_cb is not None:
                    try:
                        results.append(self._navigate_cb(arg, question))
                    except Exception:  # noqa: BLE001
                        results.append(f"Couldn't navigate to {arg}.")
                else:
                    results.append(f"I can't navigate here ({arg}).")
                continue
            act = _resolve_action(name)
            if act is not None and act.kind == "ax":           # ADR-021: GUI actuation verb
                # ADR-044 firewall: drop a spurious ax_* the model appended to a non-action turn — even
                # if the controls weren't injected, it could echo an id from a prior turn. Code disposes.
                if not self._is_ui_action_request(question):
                    continue
                if self._ax_dispatch is not None:
                    try:
                        results.append(self._ax_dispatch(name, arg))
                    except Exception:  # noqa: BLE001
                        results.append(f"Couldn't act on the screen ({name}).")
                else:
                    results.append(f"I can't control on-screen elements here ({name}).")
                continue
            if act is not None and act.kind == "habit":        # ADR-027: introspection / pruning
                if self._habit_admin is not None:
                    try:
                        results.append(self._habit_admin(name, arg))
                    except Exception:  # noqa: BLE001
                        results.append("Couldn't read the habit list right now.")
                else:
                    results.append("Habit tracking isn't available here.")
                continue
            if act is not None and act.kind == "nav":          # ADR-022: self-navigating recipe
                if self._nav_dispatch is not None:
                    try:
                        results.append(self._nav_dispatch(name, arg))
                        self._record_habit(name, arg)          # ADR-026: user-asked -> habit evidence
                    except Exception:  # noqa: BLE001
                        results.append(f"Couldn't do that ({name}).")
                else:
                    results.append(f"I can't do that here ({name}).")
                continue
            # Deterministic guards (v1.8.2 / ADR-040): each diag SENSOR runs ONLY when the user's text
            # shows the matching intent — so an unrelated question can never get a CPU/memory dump (or
            # a sound report), no matter what the 7B emits. Code disposes; the prompt only proposes.
            if name == "report_system" and not self._is_system_query(question):
                continue
            if name == "audio_status" and not self._is_audio_query(question):
                continue
            if name == "network_status" and not self._is_network_query(question):
                continue
            if name == "largest_apps" and not self._is_apps_query(question):
                continue
            if self._action_runner is None:
                continue
            try:
                result, spec = self._action_runner.propose(name, arg)
            except Exception:  # noqa: BLE001 — an action hiccup must never crash the turn
                continue
            if spec is None:
                if result is not None:
                    # ADR-045: a system report for a neutral data question ("which app uses the most
                    # memory") drops the unsolicited "nothing looks wrong" verdict — the user didn't
                    # ask whether anything was wrong. A real anomaly line is kept (drop_ is selective).
                    if name == "report_system" and not self._is_health_query(question):
                        result = drop_nominal_verdict(result)
                    results.append(result)
                    self._record_habit(name, arg)          # ADR-026: user-asked -> habit evidence
            elif self._consent_opener is not None:                 # destructive -> gate it
                try:
                    self._consent_opener(spec.label, spec.on_approve)
                    results.append(f"⏳ Awaiting your approval: {spec.label}")
                except Exception:  # noqa: BLE001
                    results.append(f"Couldn't request approval for: {spec.label}")
            else:                                                  # no consent channel -> never run it
                results.append(f"That needs confirmation, which isn't available here: {spec.label}")
        return results

    def forget(self, text: str) -> list[str]:
        """Soft-delete a memory by exact text or nearest semantic match (ADR-009). Undoable via
        `restore`. Returns the memory text(s) actually tombstoned."""
        return self._forget_facts([text])

    def restore(self, text: str) -> bool:
        """Reactivate a tombstoned memory (evicting the current slot holder to keep the invariant)."""
        return self._store.restore(text)

    def _remember_facts(self, facts: list[str], *, source: str) -> list[str]:
        """Persist auto-extracted memories (ADR-008/009). The English store is the system of record
        (guaranteed recall) and now carries an embedding for ranked retrieval + slot supersedence;
        feeding ONA via `learn` is opportunistic — a gate rejection or model hiccup never blocks the
        save. Returns the facts actually stored, for the ack."""
        saved: list[str] = []
        for fact in facts:
            if is_volatile(fact):
                continue                                       # ADR-010: never persist transient facts
            embedding = self._embed(fact)
            if not self._store.remember(fact, source=source, embedding=embedding):
                continue                                       # already known (a revisit)
            try:
                self.learn(fact)  # best-effort: enrich ONA when the fact fits the claim schema
            except Exception:  # noqa: BLE001 — gate/parse rejection or model hiccup never blocks
                pass
            saved.append(fact)
        return saved

    def _forget_facts(self, forgets: list[str]) -> list[str]:
        """Soft-delete memories the user retracted, by EXACT (then normalized) text match against the
        active set. Deliberately NOT embedding-nearest: similar siblings ("likes tea" vs "likes
        coffee") have near-identical vectors, so a nearest-match forget can tombstone the wrong one.
        Tombstoned + undoable; an unmatched forget is a safe no-op (reported, never a wrong delete)."""
        gone: list[str] = []
        for f in forgets:
            if self._store.forget(f) or self._store.forget_normalized(f):
                gone.append(f)
        return gone

    def _embed(self, text: str) -> list[float] | None:
        """Embed text via the wired embedder, or None (tests / offline) — never raises."""
        if self._embedder is None:
            return None
        try:
            return self._embedder.embed(text)
        except Exception:  # noqa: BLE001
            return None

    def _held_self_facts(self, limit: int = 40) -> list[tuple[str, str]]:
        """The single-valued self-facts we hold (slot_id, value), for output grounding (ADR-014).
        Drawn from taught facts + conversational memories; the most-protected value wins per slot."""
        texts = [f.english for f in self._store.facts_for_reload(limit=limit)
                 if getattr(f, "english", None)]
        texts += self._store.memories_for_recall(limit=limit)
        held: dict[str, str] = {}
        for text in texts:
            s = slot_of(text)
            if s is not None:
                held.setdefault(s[0], s[1])   # facts_for_reload is pinned/recent-first -> canonical wins
        return list(held.items())

    def _recall(self, question: str = "", limit: int = 30) -> str:
        """The persistent-memory bridge: English facts injected as context. Merges what the user
        explicitly taught (`facts.english`) with conversational memories — the latter **ranked by
        embedding relevance to `question`** (ADR-009) when an embedder is wired, else most-recent.
        ONA/L2 is a memory provider, not a gatekeeper."""
        taught = [f.english for f in self._store.facts_for_reload(limit=limit)
                  if getattr(f, "english", None)]
        qvec = self._embed(question) if question else None
        mems = (self._store.search(qvec, k=limit) if qvec is not None
                else self._store.memories_for_recall(limit=limit))
        lines: list[str] = []
        seen: set[str] = set()
        for text in taught + mems:
            if text and text not in seen:
                seen.add(text)
                lines.append(f"- {text}")
        return "\n".join(lines)

    def _converse_grounded(self, question: str) -> str:
        """Legacy hallucination-proof path: English -> ONA query -> cited verdict (no LLM knowledge)."""
        try:
            claims = self._translator.claims(question, QUESTION_SYSTEM_PROMPT)
        except Exception:  # noqa: BLE001 — unreadable question must not crash the REPL
            claims = []
        if not claims:
            return self._voice.say_unknown("I couldn't read that as a question I can answer.")
        claim = claims[0]
        term = statement_term(to_narsese(claim))
        answer = self.ask(term + "?")  # existing path: query L1, reload from L2 on a miss
        if answer is None or answer.truth is None:
            return self._voice.say_unknown()
        polarity, band = assess(answer.truth.frequency, answer.truth.confidence)
        # Polarity-correct statement: flip the claim's negation when ONA says it's false.
        shown = claim if polarity is not Polarity.NO else replace(claim, negated=not claim.negated)
        statement = back_render(shown).rstrip(".")
        # The audit trail is the product: dedup identical canonical premises (ONA may cite the same
        # belief via several evidence ids) and render each through one fallback — English alias if the
        # L2 store has one, else the clean canonical Narsese term (expert `tell` path).
        evidence: list[str] = []
        seen: set[str] = set()
        for premise_term in self._brain.evidence_terms(answer.stamp):
            if premise_term in seen:
                continue
            seen.add(premise_term)
            fact = self._store.get(premise_term)
            evidence.append(fact.english if (fact and fact.english) else premise_term)
        verdict = Verdict(polarity, band, statement, answer.truth.confidence,
                          answer.truth.frequency, evidence)
        return self._voice.say(verdict)

    def act(self, op_name: str, arg_name: str, stats: DecisionStats):
        """Route a proposed action through the C4 decision gate to the wired executor.

        Returns the `Proposal` (so the caller sees the autonomy decision), or None if no executor
        is wired. The executor enforces every safety constraint — closed catalog, autonomy floors,
        network gate, live allowlist, env-filter — before anything reaches the engine.
        """
        if self._executor is None:
            return None
        proposal = decide(op_name, arg_name, stats)  # raises on an unregistered operation
        self._executor.execute(proposal)
        return proposal
