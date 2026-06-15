# triage

## Overview
The **document triage & corpus-aware deviation engine** (Phase 2). Turns a born-digital commercial
contract (NDA/MSA/DPA) into a salience-ranked **triage map**, extracts its operative SLA **parameters**
("within 72 hours", "$5,000,000 cap"), and flags how each one **deviates from the user's own corpus** of
previously-ingested contracts — never from market norms (stale jurisdictional data is a legal-advisory
liability).

This is a **deterministic, symbolic engine — *not* NARS.** The comparison math is plain Python over a
SQLite parameter store (`statistics.median` per-kind cohorts + a partial-order comparator); it does not use
ONA/Narsese. An LLM is touched only inside `extract.py` (the one AST-guarded model boundary) to read English
into structured parameters; everything else is model-free and deterministic. (NARS/ONA powers a *separate*
feature — general belief distillation — not this.)

## Usage
```python
from triage.map import triage_document            # PDF -> salience-ranked TriageMap (deterministic, no model)
from triage.devscan import scan_document           # full pass: extract -> persist -> deviate -> event body
from triage.paramstore import ParamStore

m = triage_document("nda.pdf")                     # structure + clause types + anchors, no LLM
# the off-loop slow pass (extract needs an LLM handle) is driven by service/triage_worker.py:
body = scan_document("nda.pdf", llm=llm, store=ParamStore("jarvis.db"))   # -> a deviation_scan event dict
```
The daemon runs `scan_document` off-loop in `service/triage_worker.py` (AC-gated, model in its own
subprocess) and surfaces the `deviation_scan` event in the Swift **Activity › Risk** panel. Bulk onboarding
("connect a folder") enqueues each PDF via `service/corpus.py` so the per-kind baseline compounds.

## Key Components
- **`structure.py`** — the `StructureSensor` (pdfplumber): a born-digital PDF → ordered clause `Span`s with
  verifiable page+bbox anchors, or a flagged DEGRADED state. Deterministic layout heuristics; no model.
- **`taxonomy.py`** — the clause-type lexicon + `classify()`: deterministic, multi-label, fail-open (an
  unrecognized clause is surfaced as `needs_review`, never dropped).
- **`map.py`** — the `TriageMap` data model + pure salience-ranked builder + the `triage_document` shell.
- **`extract.py`** — the **single sanctioned model boundary** (AST-guarded): per salient clause, 3× GBNF
  consensus → `verify_gate` grounding → structured `{raw_quote, role, value, unit, is_qualitative}`.
- **`parameter.py`** — the `Parameter` model + Normalizer + **partial-order Comparator**. Business days =
  calendar floor + open upper (`n biz ≥ n cal`, no holiday calendar); months/years = intervals (no false
  precision); qualitative terms are nullified and declared incomparable (the firewall); non-duration
  magnitudes are reported as neutral facts, not ranked (Mirror-not-Advisor).
- **`aggregator.py`** — the per-`(clause_type, role, kind)` corpus baseline (never blended) + `find_deviations`.
- **`paramstore.py`** — the `clause_parameters` SQLite store (WAL); re-ingesting a doc replaces its rows.
- **`devscan.py`** — composes the above into one scan + the server-authored `deviation_scan` event body
  (the `render` class + plain-English `detail_label` + page citation + canonical-bounds reasoning).

## Dependencies
`pdfplumber` (MIT — PyMuPDF/AGPL deliberately avoided); `language.consensus` + `language.verify_gate`
(reused by `extract.py`); `dbconn` (WAL SQLite). The deterministic core imports **no** model/LLM/AGPL
dependency — CI-enforced by the AST guard in `test_map.py`.

## Related ADRs
[ADR-059](../../docs/adrs/ADR-059-document-triage-deviation-engine.md) — this engine and the dual-engine boundary.
