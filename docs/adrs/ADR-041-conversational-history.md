# ADR-041: Sliding conversational history (short-term memory for converse)

## Status
Accepted & live-verified. Closes the short-term amnesia gap: every `converse()` turn was stateless,
so a follow-up ("what about that?") arrived with no trace of the question it followed. Suite
483 → **489**.

## Context
JARVIS had long-term memory (SQLite + ONA, ADR-008/011), live context, habits, and persona injection
(ADR-010/012/036) — but `jarvis.converse()` assembled all of them around the *bare current question*
and discarded the exchange. The cognitive layer felt coherent across weeks and incoherent across two
consecutive sentences. Constraints: a 4096-token 7B context already carrying a large prompt prefix;
the project rule that transient chatter must never hard-bake into durable state.

## Decision
A bounded, in-memory, session-scoped sliding window — `context/history.ConversationBuffer`:
- **Record:** `converse()` is now a thin wrapper that records (question, final reply) AFTER the turn
  completes — one recording point covering every return path (grounding notices, action-only tails,
  research answers alike). Render happens inside the turn, before recording — so the current question
  never appears twice.
- **Render:** a plain-text `RECENT CONVERSATION` block (oldest first, transcript-style) injected
  directly above the `User: <question>` line — the same context-injection idiom as live context /
  habits / persona. The block explicitly tells the model the turns are context, NOT durable memory.
- **Bounds:** last 6 messages (3 exchanges); each rendered message truncated (user 300 chars,
  assistant 600) — worst case ~2.4 KB ≈ 700 tokens of the 4096 budget.
- **Session boundary (the design question):** a **15-minute conversational gap**, checked lazily at
  render/observe — deliberately NOT the 45 s `IDLE_SECONDS` compute gate: that gate decides when the
  daemon may do background work; a human pause is not the end of a conversation (reading one research
  answer can exceed 45 s). Plus: cleared on daemon restart by construction (in-memory), and on the new
  `chat_clear` dispatch command (`Jarvis.clear_conversation()`, the explicit boundary).
- **Ephemerality is structural:** render-only — the buffer feeds nothing but the prompt. The memory /
  persona / habit pipelines keep reading the raw utterance exactly as before, so short-term chatter
  cannot become a durable belief through this path.

## Consequences
- **Gained:** follow-up questions work — pronouns, "spell that", "and tomorrow?" resolve against the
  last three exchanges. The 7B also sees its own prior answers, so it can stay consistent with them.
- **Paid:** up to ~700 extra prompt tokens per turn (prefill cost on the already-slow path — the
  prompt-prefix caching task folded into ADR-038 remains the structural fix for that).
- **Risk accepted:** the model may over-trust stale context inside the 15-minute window after a topic
  change; the window is small, the block is labeled, and `chat_clear` is the escape hatch.
- jarvis.py's converse split (`converse` wrapper / `_converse_inner` flow) is mechanical, no logic
  change inside the flow.

## Alternatives Considered
- **A `conversation_history` SQLite table** (the original blueprint) — rejected: the same blueprint
  required clearing it on boot, so persistence bought nothing; a deque gives identical semantics with
  zero schema, zero eviction job, zero growth management, and no per-turn disk writes.
- **Raw chat-template markup (`<|im_start|>…`) in the prompt** — rejected: `generate_text()` feeds
  content strings to `create_chat_completion`, where llama.cpp applies the model's chat template
  itself; injecting template tokens inside content is fragile across models and breaks the uniform
  plain-block injection idiom every other context source uses.
- **Clearing on the 45 s idle decay** — rejected: it would re-create the amnesia bug for any user who
  reads an answer before following up; idle-compute gating and conversation boundaries are different
  concepts with coincidentally similar names.
- **Unbounded persistence until manual clear** — rejected: a morning question steering an unrelated
  evening question is the stale-context failure; conversations end, and 15 minutes of silence is a
  conservative proxy for that.
