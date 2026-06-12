# ADR-056: The Dual-Brain Multiplexer & Auditable Egress Seam

> Numbering note: this is **ADR-056**, not "054" — ADR-054 is the NL Intent Router and ADR-055 is the
> unified workspace window. (Truth over tidiness.)

## Status
**Accepted** (ratified). Build in progress (backend-only; the client stays unaware of two brains until
the backend proves it juggles them invisibly):
- **Phase 1 — `cloud_egress.py`** (seam + contextual firewall + custom OpenAI HTTP wrapper) — landed,
  headless-tested, fully isolated (injected transport, no real network).
- **Phase 2 — Multiplexer + off-loop execution + Anthropic** — landed and headless-tested:
  - `language/multiplexer.py` — the `Brain` (same 3-method surface as `LocalLLM`); routes private→local,
    general→`cloud_egress`. **Output unification proven**: cloud strict-JSON `{"claims":[…]}` is unwrapped
    to the bare array so `parse_claims`/`validate_intent` run identically on either brain. Per-request
    `CloudContext` (mode/key/provider/model); `clear_cloud()` drops the key (daemon credential-stateless).
  - `service/cloud_job.py` — off-loop executor: runs the cloud call in a background thread, signals via a
    self-pipe fd the daemon `select()`s. **Concurrency verified** (`test_offloop_cloud_call_does_not_stall
    _the_select_loop`): a ~1.2 s in-flight cloud call lets the loop drain ~every 50 ms telemetry frame —
    no dropped Sentinel frames, no loop stall.
  - `cloud_egress.anthropic_complete` — provider #2; tool-use `input` serialized to a JSON string so its
    structured output is byte-shape-identical to OpenAI's.
  - `make_claim_source()` now returns the Multiplexer (default private → behavior unchanged).
  - Suite 558 → 571.
- **Phase 3 — socket wiring + live concurrency check + the full client UI** — landed:
  - `session.py` — `cloud_ask` (General Mode, off-loop CloudJob, per-request key, async `cloud_answer`
    event) + `egress_log` (Privacy Receipts). Registered in extra_fds/handle_fd.
  - **LIVE-FIRE concurrency check passed** (`test_cloud_concurrency.py`): the REAL daemon over a REAL
    socket, a 1.5 s cloud call in flight — 21 concurrent `status` round-trips, max **3.7 ms**, median
    **0.4 ms**. The select loop never blocked → the Sentinel's sensor fd (same loop) cannot drop a frame.
    A second test proves a cloud failure becomes a recovery event, not a crash.
  - Swift UI: `CloudMode.swift` (Keychain key storage, one-time disclosure sheet framed by negative
    space, key-entry sheet); the `[🔒 On-device] / [☁️ Cloud]` composer toggle with idle auto-revert;
    the inline egress footer in Chat; the Privacy Receipts section in the Identity tab. All three surfaces
    render-verified (PNG).
  - NFR-1/2 amended in the README + `language/llm.py` header (no silent edits).
  - Suite 558 → 573.
- **Remaining (follow-ons, tracked):** cloud claim-extraction → local NARS ingest (a chained off-loop
  job — today the cloud answer feeds persona-observe but not yet ONA); the "Default brain" power-user
  setting (deliberately withheld per ratification — "let the power users complain first"); cloud-without-
  any-local-model (today the Multiplexer wraps a local model; cloud requires a GGUF present).

**Ratified rulings (binding):**
- **Key delivery:** the signed Swift client passes the API key **per-request over the local socket**;
  the daemon stays **stateless** about credentials (key lives only in the active worker's memory, gone
  when the socket closes). No daemon-side Keychain access.
- **Egress visibility:** **both** a per-turn inline indicator **and** a rolling session **egress ledger**
  in the Cognitive Identity tab (timestamp · destination host · byte size · sanitized payload schema).
- **Provider #2:** **Anthropic (Claude)** — best structured entity-relation extraction for NARS.

**Build-phase verification (wired into the harness):**
- **12 s hard egress timeout** (`EGRESS_TIMEOUT`) — no zombie workers on a dropped/handed-off connection.
- **Structured failure interception** — `auth` / `rate_limit` / `timeout` / `bad_response` are returned
  as a `CloudResult` (→ a Chat recovery card), never a crash/hang.
- **Socket payload overhead** — verified when the per-request key path is wired (the key must not stall
  the daemon's `select()` loop).

## Context
We hit the hard ceiling of local-first compute. A quantized 7B doing live multi-hop web research is
**slow (40–60 s) and shallow** — for general/web tasks it's not useful as an everyday assistant (the
user's verdict, confirmed by the FIFA-fixtures failure). The fix is not to keep optimizing a losing
battle but to let the user **choose the brain per task**:

- **Private Mode** (today's behavior): the local 7B, full tool catalog, never leaves the machine.
- **General Mode**: a user-supplied frontier model (OpenAI first) for fast, robust general work.

Both brains feed the same symbolic memory (NARS ingests *Narsese*, not raw text), so the cloud brain
makes the local NARS **smarter** without owning the memory graph.

This is a real pivot, and it **breaks a foundational invariant** — so this ADR amends it honestly
rather than stealth-editing it.

## Decision

### Vector 1 — Amend NFR-1/2 (the air-gap), explicitly
`language/llm.py` and the README currently assert **"strictly local / air-gapped (NFR-1/2): no network
at runtime,"** and `safespawn.scrub_environ()` strips every secret-bearing env var at boot so a key can
never reach a child. This ADR **redefines NFR-1/2**:

> **NFR-1/2 (amended):** JARVIS is **air-gapped by default and private-first**. Network egress to a
> third-party model occurs **only** when the user explicitly engages General Mode, **only** through the
> single auditable egress seam (Vector 2), and **only** with the data that seam is permitted to send.
> Private Mode remains strictly local, zero-TCC, zero-egress. The default is Private.

The README's "air-gapped" language and `llm.py`'s header comment are updated in the same change. No
silent edits — the relaxation is documented, scoped, and default-off.

### Vector 2 — The Egress Seam & the Contextual Firewall
A single sanctioned module — **`cloud_egress.py`** — is the *only* place in the codebase that performs a
network request to a model provider. It is the `safespawn` of the network: the one choke point, heavily
audited, where the privacy boundary is enforced and every byte that leaves is logged.

**The Contextual Firewall (the core safety mechanism).** The seam does not accept "the current prompt
context" and trust the caller to have sanitized it. Instead it accepts a **closed, allowlisted request
envelope** and constructs the outbound payload *itself* from only those fields. It is structurally
impossible to attach private context because the seam has **no reference to** the persona store, the
`usage_events` table, the NARS memory buffer, or the grounding cache.

```
struct CloudRequest {           // the ONLY shape the egress seam accepts
    system: String              // a fixed, vetted system prompt (chosen from a constant set)
    user: String                // the user's explicit message / the text to extract from
    tools: [ExternalTool]        // EXTERNAL-only tool schemas (search_web, read_article, get_weather…)
    jsonSchema: Schema?          // for structured tasks (intent / NARS claims) — strict mode
    // NOTHING ELSE. No persona, no usage_events, no NARS graph, no grounding, no file contents.
}
```

Enforcement is **belt-and-suspenders**:
1. **By construction:** `cloud_egress` imports none of the private stores; it literally cannot read them.
2. **Allowlist, not denylist:** the outbound JSON is built field-by-field from `CloudRequest`; there is
   no "pass-through" path that could leak an extra field.
3. **Egress log:** every call appends a record (provider, endpoint, byte count, the system-prompt id,
   tool names, and a redaction-checked preview) to an auditable local log the user can inspect — so the
   boundary is *verifiable*, not merely trusted. A test (`test_egress_firewall`) asserts that a request
   built from a context containing seeded private tokens never emits them.
4. **Tool airgap:** in General Mode the Multiplexer strips local tools (`summarize_file`, `report_usage`,
   `set_volume`, …) from `tools` before the request reaches the seam. A Cloud brain literally never sees
   that `summarize_file` exists; the Intent Router catches a local-only verb and the UI prompts:
   *"That needs local file access — switch to Private Mode to run it."* The boundary is **enforced by
   the absence of the capability**, not by a prompt instruction.

### Vector 3 — Keychain & Concurrency
- **Key storage = macOS Keychain.** The API key is entered in a settings pane and stored via the
  Security framework (Swift client → Keychain). It is **never** placed in the environment —
  `safespawn.scrub_environ()` would delete it, and env vars are the classic leak vector. The daemon
  reads it on demand from the Keychain (or the client passes it per-request over the local socket; the
  ADR's open question Q1). It is never written to disk in plaintext, never logged.
- **Off-loop execution.** A cloud completion is network I/O of several seconds; run synchronously it
  would freeze the single-threaded select loop exactly like a blocking local inference (ADR-003). The
  `CloudDriver` runs **off-loop** using the proven offload pattern (ADR-052 / the WhisperJob seam): the
  request executes in a detached worker (or a backgrounded URLSession whose completion fd the daemon
  `select()`s), streaming progress back over the same mechanism, so chat / sensing / the Mirror keep
  flowing while a cloud call is in flight.

### Vector 4 — The Multiplexer Interface
The rest of the system (NARS claim extractor, Intent Router, voice formatter, tool executors) must stay
**unaware** of which brain is running. Today they call one of three methods on the local `LocalLLM`:
`generate(system, sentence)` (GBNF claims), `generate_json(system, user, grammar)` (GBNF intent), and
`generate_text(system, user, max_tokens)` (free text). The Multiplexer implements the **same three
methods** behind a shared protocol and routes to the active driver:

```
protocol Brain {                         // identical surface to today's LocalLLM
    func generate(system, sentence) -> String          // structured claims
    func generateJSON(system, user, schema) -> String  // structured intent
    func generateText(system, user, maxTokens) -> String
}

Multiplexer(Brain):                      // injected where make_claim_source() is today
    mode: .private | .general            // global toggle (default .private)
    local:  LocalLLMDriver               // wraps today's llama.cpp LocalLLM (GBNF)
    cloud:  CloudDriver                  // wraps cloud_egress + OpenAI strict json_schema

    generate / generateJSON / generateText:
        if mode == .private:  return local.<m>(…)        // unchanged path
        else:                 return cloud.<m>(…)         // via the egress seam
```

**Output unification** is the contract that lets callers stay ignorant of the brain:
- **Structured tasks** (intent, NARS claims): local uses our **GBNF grammar** (guaranteed-valid tokens);
  cloud uses **OpenAI Structured Outputs (`response_format: json_schema`, `strict: true`)** — the only
  cloud feature with the same hard guarantee. Both return the *exact same parsed dict*; the existing
  `validate_intent` / `parse_claims` run unchanged on either.
- **Free text** (voice formatter, answers): both return a string; cloud answers are still validated by
  `language.voice.sanitize_voice` exactly as local ones are.

The Multiplexer is the **only** new injection point; `make_claim_source()` returns a Multiplexer instead
of a bare `LocalLLM`, and nothing downstream changes.

### The NARS ruling — Cloud extraction, contextually firewalled (ratified)
In General Mode, claim-extraction runs on the **cloud** model (higher-fidelity ontology than the 7B, and
the cloud already holds that text — no new exposure). But the extraction request goes through the same
Contextual Firewall: the cloud sees **only** `[fixed "extract Narsese" system prompt] + [current user
prompt] + [current cloud response]` — **never** the NARS memory buffer, the Cognitive-Identity baseline,
or `usage_events`. The extracted `RelationClaim`/`PropertyClaim` objects come back through the egress
seam and the **local** ONA ingests them. The cloud makes NARS smarter without ever seeing the memory graph.

## UI (per the ratified plan, detailed in a later UI ADR)
- A persistent **Local 🔒 / Cloud ☁️** toggle in the Universal Composer input bar, bound to the
  Multiplexer mode (default Local). When Cloud is active, the Intent Router grays out local-only verbs.
- A settings pane to paste the API key (→ Keychain) and pick the provider.
- General Mode shows a subtle, honest indicator that *this turn leaves your machine* (egress is visible).

## Consequences
- General tasks get frontier speed/quality; sensitive tasks stay strictly local. The user owns the trade.
- One auditable egress choke point + an allowlist envelope make the privacy boundary *verifiable*, not
  trusted — the property a privacy-first app cannot lose.
- New dependency surface is **one small, audited HTTP wrapper** (no LiteLLM) — minimal supply-chain risk.
- The cloud brain improves local NARS (better extraction) while the memory graph never leaves.
- Cost/honesty: the README and NFR are amended; the default remains private; nothing leaves without an
  explicit user toggle and a visible indicator.

## Resolved (see ratified rulings in Status)
1. Key delivery → client passes per-request over the socket; daemon stateless re credentials.
2. Egress visibility → per-turn indicator **and** a session ledger in the Cognitive Identity tab.
3. Provider #2 → Anthropic.
