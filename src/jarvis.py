"""NARS-JARVIS application orchestrator — composes brain (L1) + language + memory (L2).

The M0 capability-C1 loop: `learn(english)` writes through to L2 and feeds L1; `ask(question)`
answers from L1 with an evidence trail, and on a cache miss reloads from L2 and retries.
Composes domains via their public interfaces only (ADR-001). Imperative Shell (S-02).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from brain import Brain, canonical_input, input_accepted
from context import conflicting_habit, grounding_notice, is_volatile
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
                 sentinel_beliefs_provider: Callable[[], list[tuple[str, float, float]]] | None = None,
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
        # Dynamic context (ADR-010): a shell-provided callable returning the fresh live-facts block
        # (date/time + system + foreground) injected each turn. None => no live context (tests/offline).
        self._context_provider = context_provider
        # Learned habits (ADR-012): a callable returning the translated "Learned habits" block from the
        # sentinel's persisted beliefs. None => no habits block (tests/offline).
        self._habits_provider = habits_provider
        # Pre-commit grounding (ADR-013): a callable returning the raw persisted sentinel beliefs, so
        # converse can drop conversational memories that try to control a sentinel-governed category.
        self._sentinel_beliefs_provider = sentinel_beliefs_provider
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
        memory = self._recall(question)
        live = self._context_provider() if self._context_provider is not None else ""
        habits = self._habits_provider() if self._habits_provider is not None else ""
        blocks: list[str] = []
        if live:
            blocks.append(live)
        if habits:                                            # ADR-012: learned preferences to respect
            blocks.append(habits)
        if memory:
            blocks.append("Persistent memory (the user taught you these; treat as ground truth):\n"
                          + memory)
        blocks.append(f"User: {question}")
        user = "\n\n".join(blocks)
        try:
            reply = self._assistant.generate_text(ASSISTANT_SYSTEM_PROMPT, user, max_tokens=512)
        except Exception:  # noqa: BLE001 — a model hiccup degrades to the grounded path, never crashes
            return self._converse_grounded(question)
        # Auto-memory (ADR-008/009): pull [[REMEMBER]]/[[FORGET]] directives, persist them, show the
        # cleaned reply, and confirm changes so a wrong save/forget is visible and correctable.
        clean, facts = split_memory_directives(reply)
        clean, forgets = split_forget_directives(clean)
        clean = clean.strip()
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
        acks = [a for a in (memory_acknowledgment(saved),
                            ("(Forgot: " + "; ".join(forgotten) + ")") if forgotten else "") if a]
        if not clean:  # no prose: show the confirmation if we acted, else fall back to grounded
            return " ".join(acks) if acks else self._converse_grounded(question)
        return f"{clean}\n{' '.join(acks)}" if acks else clean

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
