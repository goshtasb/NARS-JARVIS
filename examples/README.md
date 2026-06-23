# Grounding Gate — zero-config demo

**The LLM proposes a structured claim; the source text — not the model — grades it.**

This runs the *real* production gate ([`src/language/verify_gate.py`](../src/language/verify_gate.py),
pure stdlib: `re` + `dataclasses`) on a handful of contract clauses. No local model, no download, no
API key, no daemon. It imports the exact code that guards what reaches the NARS memory vault and shows
its verdict on each proposed claim.

## Run it (2 seconds, any platform)

```sh
python3 examples/grounding_gate_demo.py
```

Deterministic — identical verdicts on every machine, every run:

```
• Fabricated number: 1.5%  ->  '1500 basis points'
    source clause : ... overdue amounts shall accrue interest at 1.5% per month.
    LLM proposed  : RelationClaim  ->  '1500 basis points per month'
    gate verdict  : REJECTED  (fail-closed — never written to memory)
    reasons       : L2:ungrounded_values=['1500', 'basis', 'points']
```

| Failure mode | Verdict | Gate reason (real output) |
| --- | --- | --- |
| `1.5%` → `1500 basis points` | REJECTED | `L2:ungrounded_values=['1500', 'basis', 'points']` |
| 12-month look-back as deadline | DEGRADED | `L3:temporal_stripped(role=window;[])` |
| Invented "Regulator" obligation | REJECTED | `L1:evidence_not_in_source` |
| Dropped "Neither" | REJECTED | `L5:negation_inversion` |
| Faithful 72-hour deadline | ADMITTED | `ok` |

## Skeptic mode (`--live`)

```sh
OPENAI_API_KEY=sk-... python3 examples/grounding_gate_demo.py --live
# or: ANTHROPIC_API_KEY=... python3 examples/grounding_gate_demo.py --live --provider anthropic
```

Prompts a real cloud model with the production extractor prompt
([`guarded_extract.GUARDED_PROMPT`](../src/language/guarded_extract.py)) over the same clauses, then
runs its live output through the same gate. The local build constrains the model with a GBNF grammar;
over the network we can only *prompt* for JSON and parse leniently, so a live model may occasionally
emit unparseable output — that is a property of the model, not the gate.

## How it grades (the five layers)

It abandons "all tokens verbatim" fragility for morphology-aware grounding:

- **L1** — the `evidence` quote must be a verbatim token-subsequence of the source.
- **L2** — every numeric token must match **exactly**; every content word must be morphologically
  traceable via prefix match (`liable` grounds against `liability`), **skipping stop-words and tokens
  under 3 characters**.
- **L3** — cue-role: a deadline slot is kept only if a deadline cue (not a window/rate/duration cue)
  governs the number; otherwise the slot is stripped to the verified relational core (DEGRADED).
- **L4** — closed-set sanity (units, deontics, numeric values).
- **L5** — a negation tripwire: if the evidence carries an inversion cue, the claim must encode it.

## Scope — what this is and is NOT

- **It is** a per-chunk extraction validator. You pass it `(claim, source)` where `source` is the
  **chunk the claim was extracted from** — not your whole corpus.
- **Retrieval and chunking are your job.** Pass a 50k-token raw PDF as `source` and L1 degrades:
  short evidence quotes can match *somewhere* in a large blob by coincidence (false-positive
  grounding), and the scan cost grows with source length. Give the gate the minimal relevant span.
- **It is NOT** ONA's non-axiomatic reasoning, cross-belief contradiction detection, temporal
  reasoning, or the end-to-end two-brain runtime. This is the deterministic write-gate, in isolation.
