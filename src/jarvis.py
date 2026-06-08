"""NARS-JARVIS application orchestrator — composes brain (L1) + language + memory (L2).

The M0 capability-C1 loop: `learn(english)` writes through to L2 and feeds L1; `ask(question)`
answers from L1 with an evidence trail, and on a cache miss reloads from L2 and retries.
Composes domains via their public interfaces only (ADR-001). Imperative Shell (S-02).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from brain import Brain, canonical_input, input_accepted
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
    memory_acknowledgment,
    split_memory_directives,
    to_narsese,
)
from memory import (
    MemoryStore,
    MetricsStore,
    is_valid_belief,
    observe,
    reload_into_brain,
    statement_term,
    statement_truth,
)


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
    "MEMORY: When the user tells you a durable fact about themselves (e.g. their name or role), states "
    "a lasting preference, or explicitly asks you to remember something, record it by appending a "
    "directive on its own line, exactly: [[REMEMBER: <concise third-person fact>]] — one per item. "
    "Examples: user says 'my name is Ashkan' -> [[REMEMBER: the user's name is Ashkan]]; "
    "'I prefer tabs over spaces' -> [[REMEMBER: the user prefers tabs over spaces]]. "
    "Be conservative: do NOT emit it for questions, small talk, or transient details — only clear "
    "personal facts, preferences, and explicit 'remember…' requests. Write your normal reply as "
    "usual; do not mention or explain the directive — it is stripped before the user sees it."
)


class Jarvis:
    def __init__(self, translator: Translator, store: MemoryStore, brain: Brain,
                 guard: ContradictionGuard | None = None,
                 executor: Executor | None = None,
                 gate: IngestionGate | None = None,
                 metrics: MetricsStore | None = None,
                 voice: Voice | None = None,
                 assistant: object | None = None) -> None:
        self._translator = translator
        self._store = store
        self._brain = brain
        self._guard = guard
        self._executor = executor  # None => orchestrator stays learn/ask only (no execution path)
        self._gate = gate          # None => ungated learn (no semantic gate; e.g. no embedder)
        self._metrics = metrics    # None => no telemetry; gate-friction outcomes only, never text
        self._voice = voice or Voice()  # template-only by default; formatter LLM is optional
        # LLM-first brain (ADR-007): when a real model is wired, converse() lets the LLM answer from
        # its own knowledge with the user's persistent memory injected as ground truth. With no
        # assistant (tests / no model) converse() falls back to the legacy ONA-grounded path.
        self._assistant = assistant if (assistant is not None and hasattr(assistant, "generate_text")) else None

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
        return statement

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
        return True

    def ask(self, question: str):
        """Answer from L1; on a cache miss, repopulate L1 from L2 and retry once."""
        answer = self._brain.ask(question)
        if answer is None:
            reload_into_brain(self._store, self._brain)
            answer = self._brain.ask(question)
        return answer

    def converse(self, question: str) -> str:
        """LLM-first answer (ADR-007): the model answers from its own knowledge with the user's
        persistent memory injected as ground truth. Falls back to the legacy ONA-grounded path when
        no language model is wired (tests / offline demo)."""
        if self._assistant is None:
            return self._converse_grounded(question)
        memory = self._recall()
        user = (f"Persistent memory (the user taught you these; treat as ground truth):\n{memory}\n\n"
                if memory else "") + f"User: {question}"
        try:
            reply = self._assistant.generate_text(ASSISTANT_SYSTEM_PROMPT, user, max_tokens=512)
        except Exception:  # noqa: BLE001 — a model hiccup degrades to the grounded path, never crashes
            return self._converse_grounded(question)
        # Auto-memory (ADR-008): pull any [[REMEMBER: …]] directives the LLM emitted, persist them,
        # show the cleaned reply, and confirm what was saved so a wrong save is visible/correctable.
        clean, facts = split_memory_directives(reply)
        clean = clean.strip()
        if not clean:
            return self._converse_grounded(question)
        saved = self._remember_facts(facts, source=question) if facts else []
        ack = memory_acknowledgment(saved)
        return f"{clean}\n{ack}" if ack else clean

    def _remember_facts(self, facts: list[str], *, source: str) -> list[str]:
        """Persist auto-extracted memories (ADR-008). The English memory store is the system of
        record (guaranteed recall); feeding ONA via `learn` is opportunistic — a gate rejection or
        model hiccup must never block the save. Returns the facts actually stored, for the ack."""
        saved: list[str] = []
        for fact in facts:
            self._store.remember(fact, source=source)  # ALWAYS — guaranteed recall
            try:
                self.learn(fact)  # best-effort: enrich ONA when the fact fits the claim schema
            except Exception:  # noqa: BLE001 — gate/parse rejection or model hiccup never blocks
                pass
            saved.append(fact)
        return saved

    def _recall(self, limit: int = 30) -> str:
        """The persistent-memory bridge: the English facts injected as context. Merges what the user
        explicitly taught (`facts.english`) with auto-extracted conversational memories (ADR-008).
        ONA/L2 is now a memory provider, not a gatekeeper."""
        taught = [f.english for f in self._store.facts_for_reload(limit=limit)
                  if getattr(f, "english", None)]
        lines: list[str] = []
        seen: set[str] = set()
        for text in taught + self._store.memories_for_recall(limit=limit):
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
