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
from triage.parameter import Comparison, ParameterKind
from triage.preamble import extract_preamble
from triage.structure import LayoutStructureSensor, StructureSensor

_STRICT = {Comparison.TIGHTER, Comparison.LOOSER}

# Slice 3c: the deterministic enums -> plain-English reason the lawyer reads (server-authored, NEVER in
# Swift — keeps the explanation logic in the testable core). Keyed by (verdict name, detail); falls back to
# the no-detail row, then a generic. "Mirror, not Advisor": each states WHAT and WHY-it-can('t)-rank, no advice.
_DETAIL_LABELS = {
    ("TIGHTER", ""): "Stricter than your standard.",
    ("LOOSER", ""): "Looser than your standard.",
    ("LOOSER", "open_upper_ge"): "At least as long as your standard, with no fixed upper bound — so looser.",
    ("DIFFERS_IN_KIND_UNRANKABLE", "cross_kind"):
        "Can't be ranked: the units don't convert cleanly, so neither is provably stricter. Read both and decide.",
    ("DIFFERS_IN_KIND_UNRANKABLE", "ambiguous_overlap"):
        "Can't be ranked: the possible ranges overlap, so neither is provably stricter. Read both and decide.",
    ("DIFFERS_IN_KIND_UNRANKABLE", "cross_currency"): "Can't be ranked: different currencies.",
    ("DIFFERS_IN_KIND_UNRANKABLE", "neutral_magnitude"): "Differs in amount — shown for review, not ranked.",
    ("DIFFERS_IN_KIND_UNRANKABLE", "unknown"): "Couldn't interpret one of the values — review manually.",
    ("INCOMPARABLE_QUALITATIVE", "qualitative"): "Qualitative term — manual review required.",
    ("EQUAL", ""): "Matches your standard.",
}


def detail_label(verdict) -> str:
    """Plain-English reason for a finding (None == new-to-corpus). Server-authored; the Swift client renders it."""
    if verdict is None:
        return "New to your corpus — no prior standard to compare against."
    return _DETAIL_LABELS.get((verdict.result.name, verdict.detail or ""),
                              _DETAIL_LABELS.get((verdict.result.name, ""), "Flagged for review."))


def _num(x) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def _hours(x) -> str:
    return f"{int(x)}h" if float(x).is_integer() else f"{x:.1f}h"


def _canon_label(p) -> str | None:
    """The canonical interpretation the deterministic comparator actually used — for the 'show the reasoning'
    disclosure. None when there is nothing to normalize (qualitative)."""
    if p.is_qualitative or p.kind is ParameterKind.QUALITATIVE or p.canon_lo is None:
        return None
    if p.kind is ParameterKind.DURATION_BUSINESS:
        return f"≥ {_hours(p.canon_lo)} (open upper — depends on weekends/holidays)"
    if p.kind is ParameterKind.DURATION_CALENDAR:
        if p.canon_hi is not None and p.canon_lo != p.canon_hi:
            return f"{_hours(p.canon_lo)}–{_hours(p.canon_hi)}"            # length-ambiguous interval
        return _hours(p.canon_lo)
    return f"{_num(p.value)} {p.unit}".strip() if p.value is not None else None   # money / percent / count


def _cohort_canon_label(cohort) -> str | None:
    if cohort is None:
        return None
    if cohort.kind in ("duration_calendar", "duration_business"):
        return _hours(cohort.median)
    if cohort.kind == "percent":
        return f"{_num(cohort.median)}%"
    return _num(cohort.median)


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
    this_canon = _canon_label(p)
    return {
        "clause_type": p.clause_type,
        "role": p.role,
        "page": p.anchor.page,                            # citation provenance (Slice 3c surfaces it on the glass)
        "render": render_class(v),
        "verdict": v.result.name if v else None,          # "TIGHTER" | "LOOSER" | ... (locked enum name)
        "detail": (v.detail or None) if v else None,
        "detail_label": detail_label(v),                  # Slice 3c: the plain-English reason (always shown)
        "this": {"raw_quote": p.raw_quote, "value": p.value, "unit": p.unit, "kind": p.kind.value},
        "baseline": ({"kind": cohort.kind, "median": cohort.median, "n": cohort.n} if cohort else None),
        # Slice 3c: canonical bounds for the optional "show the reasoning" disclosure (None == nothing to show)
        "reasoning": ({"this": this_canon, "standard": _cohort_canon_label(cohort)}
                      if this_canon is not None else None),
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
