"""Slice 1: the Triage-Map data model + pure builder + public entry point (Functional Core / thin Shell).

`build_triage_map` is pure: DocumentStructure (+ preamble) -> a salience-ranked TriageMap. Anchors pass
through VERBATIM from the deterministic sensor — never recomputed or invented (a citation that doesn't land
is a navigation lie). `triage_document` is the thin imperative shell that does the file I/O. Degraded
structures still yield a flagged map (page-anchored entries), never an empty silent one.
"""
from __future__ import annotations

from dataclasses import dataclass

from triage.preamble import extract_preamble
from triage.structure import Anchor, DocumentStructure, LayoutStructureSensor, StructureSensor
from triage.taxonomy import RISK_WEIGHT, ClauseType, classify

_PREVIEW = 160


@dataclass(frozen=True)
class TriageEntry:
    number: str | None
    heading: str | None
    types: tuple[str, ...]                 # ClauseType .value names; ("unclassified",) when unknown
    salient: bool
    needs_review: bool
    risk_weight: int
    anchor: Anchor
    preview: str


@dataclass(frozen=True)
class TriageMap:
    document_type: str | None
    parties: tuple[str, ...]
    page_count: int
    entries: tuple[TriageEntry, ...]       # salience-ranked
    degraded: bool
    degraded_reason: str


def _preview(text: str) -> str:
    one = " ".join((text or "").split())
    return one[:_PREVIEW] + ("…" if len(one) > _PREVIEW else "")


def build_triage_map(structure: DocumentStructure, document_type: str | None,
                     parties: tuple[str, ...]) -> TriageMap:
    """Pure: structure + preamble -> ranked TriageMap. No I/O, no model, no clock."""
    entries: list[TriageEntry] = []
    for span in structure.spans:
        c = classify(span.text, span.heading)
        type_names = tuple(t.value for t, _ in c.types) or (ClauseType.UNCLASSIFIED.value,)
        weight = max((RISK_WEIGHT[t] for t, _ in c.types), default=RISK_WEIGHT[ClauseType.UNCLASSIFIED])
        entries.append(TriageEntry(
            number=span.number, heading=span.heading, types=type_names,
            salient=c.salient, needs_review=c.needs_review, risk_weight=weight,
            anchor=span.anchor, preview=_preview(span.text),
        ))
    # Rank: salient first by descending risk; ties keep document order (stable sort).
    order = {id(e): i for i, e in enumerate(entries)}
    ranked = tuple(sorted(entries, key=lambda e: (not e.salient, -e.risk_weight, order[id(e)])))
    return TriageMap(document_type, parties, structure.page_count, ranked,
                     structure.degraded, structure.reason)


def triage_document(path: str, sensor: StructureSensor | None = None) -> TriageMap:
    """Thin imperative shell: parse the PDF, read page-1 preamble, build the map. The only I/O entry point."""
    sensor = sensor or LayoutStructureSensor()
    structure = sensor.parse(path)
    first_page = "\n".join(s.text for s in structure.spans if s.anchor.page == 1)
    doc_type, parties = extract_preamble(first_page)
    return build_triage_map(structure, doc_type, parties)
