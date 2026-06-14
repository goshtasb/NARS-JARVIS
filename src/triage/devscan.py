"""Slice 3a: the deviation-scan orchestrator + the pure event-contract serializer (Functional Core).

Composes the Slice 1 + Slice 2 libraries into one off-loop pass over a single document:

    re-parse file -> SALIENT spans only -> extract_parameters (the Slice 2 guarded model boundary)
                  -> persist to the ParamStore -> build the per-kind baseline (this doc EXCLUDED)
                  -> find_deviations -> serialize to the frozen UI event body.

Why re-parse instead of reading the TriageMap: a TriageEntry carries only a 160-char PREVIEW (map.py), not
the full clause body the extractor needs. Re-parsing recovers the verbatim Span.text while reusing the map's
exact `salient` decision (same `classify`), so salience never diverges. The LLM is INJECTED (never imported
here), so this stays out of the model-import path and the AST guard — the worker is the only model boundary.

The serializer enforces the "Mirror, not Advisor" guardrail at the contract layer: each surfaced finding
carries a `render` class the UI binds to literally (strict / neutral / unrankable / qualitative), so the
glass never has to re-derive whether a magnitude may be coloured as strictness.
"""
from __future__ import annotations

import hashlib
import os

from triage.aggregator import build_baseline, find_deviations
from triage.extract import extract_parameters
from triage.map import build_triage_map
from triage.parameter import Comparison
from triage.preamble import extract_preamble
from triage.structure import LayoutStructureSensor, StructureSensor

_STRICT = {Comparison.TIGHTER, Comparison.LOOSER}


def file_doc_id(path: str) -> str:
    """Stable content hash of the source file (the ParamStore doc_id). Re-ingesting the same bytes replaces."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def render_class(verdict) -> str:
    """The UI rendering guardrail, decided once, server-side. None == new-to-corpus (not surfaced)."""
    if verdict is None:
        return "informational"
    r = verdict.result
    if r is Comparison.INCOMPARABLE_QUALITATIVE:
        return "qualitative"                      # neutral info badge: "manual review required"
    if r in _STRICT:
        return "strict"                           # duration TIGHTER/LOOSER -> amber, both quotes shown
    if r is Comparison.DIFFERS_IN_KIND_UNRANKABLE:
        return "neutral" if verdict.detail == "neutral_magnitude" else "unrankable"  # money/% vs can't-rank
    return "equal"                                # EQUAL -> not surfaced


def is_surfaced(verdict) -> bool:
    """A finding worth showing in the panel: it has a verdict and it is not EQUAL (and not new-to-corpus)."""
    return verdict is not None and verdict.result is not Comparison.EQUAL


def _finding_dict(finding) -> dict:
    p, v, cohort = finding.param, finding.verdict, finding.cohort
    return {
        "clause_type": p.clause_type,
        "role": p.role,
        "page": p.anchor.page,
        "render": render_class(v),
        "verdict": v.result.name if v else None,          # "TIGHTER" | "LOOSER" | ... (locked enum name)
        "detail": (v.detail or None) if v else None,
        "this": {"raw_quote": p.raw_quote, "value": p.value, "unit": p.unit, "kind": p.kind.value},
        "baseline": ({"kind": cohort.kind, "median": cohort.median, "n": cohort.n} if cohort else None),
    }


def build_scan_body(doc: str, doc_id: str, salient_count: int, findings: list) -> dict:
    """Pure: findings -> the frozen `deviation_scan` event body. State is populated/empty (terminal); the
    daemon emits the `pending` state separately while the worker runs."""
    surfaced = [_finding_dict(f) for f in findings if is_surfaced(f.verdict)]
    return {"doc": doc, "doc_id": doc_id, "salient_count": salient_count,
            "state": "populated" if surfaced else "empty", "findings": surfaced}


def scan_document(path: str, *, llm, store, sensor: StructureSensor | None = None,
                  on_pending=None, doc_id: str | None = None) -> dict:
    """Run one document's deviation scan and return the event body. `store` is a ParamStore (this doc's rows
    are replaced, then excluded from its own baseline). `on_pending(salient_count)` fires once the salient
    set is known (before the slow extraction) so the daemon can paint the Pending state with no layout shift.
    """
    sensor = sensor or LayoutStructureSensor()
    structure = sensor.parse(path)
    text_by_anchor = {s.anchor: s.text for s in structure.spans}
    first_page = "\n".join(s.text for s in structure.spans if s.anchor.page == 1)
    doc_type, parties = extract_preamble(first_page)
    salient = [e for e in build_triage_map(structure, doc_type, parties).entries if e.salient]
    if on_pending is not None:
        on_pending(len(salient))

    doc_id = doc_id or file_doc_id(path)
    params = []
    for entry in salient:
        clause_text = text_by_anchor.get(entry.anchor, "")
        if not clause_text.strip():
            continue
        clause_type = entry.types[0] if entry.types else "unclassified"
        params.extend(extract_parameters(llm, clause_text, clause_type=clause_type, anchor=entry.anchor))

    store.add_parameters(doc_id, params)
    baseline = build_baseline(store.rows(exclude_doc_id=doc_id))
    findings = find_deviations(params, baseline)
    return build_scan_body(os.path.basename(path), doc_id, len(salient), findings)
