"""Slice 1: the StructureSensor — deterministic, layout-based document structure (Functional Core, S-02).

Turns a born-digital PDF into an ordered list of clause/section Spans with verifiable page+bbox anchors —
or a flagged DEGRADED state when it cannot. Structure is DETERMINISTIC (layout heuristics only, no model,
no clock, no randomness); clause-TYPE classification is a separate concern (taxonomy.py). The pdfplumber
backend is encapsulated behind the StructureSensor protocol so a future VLM sensor (for scanned docs) drops
in without touching callers. NEVER fabricates structure: a scanned/no-text PDF degrades to no_text_layer;
flat prose degrades to a page-anchored best-effort map (no_structure_detected).
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Protocol

import pdfplumber

_LINE_TOL = 3.0                       # words within this vertical distance are the same line
_MIN_TEXT_CHARS = 20                  # below this (whole doc) -> treat as no text layer (scanned/image)
_HEADING_SIZE_RATIO = 1.10           # a line >= 110% of body font is a heading candidate
_HEADING_MAX_CHARS = 80              # headings are short; long lines are body even if bold/large

_NUM_PATTERNS = (
    re.compile(r"^\s*(\d+(?:\.\d+)+)\s+\S"),                          # 1.1  1.1.1  (multi-level decimal)
    re.compile(r"^\s*(\d+)\.\s+\S"),                                  # 1.  7.       (integer + trailing dot)
    re.compile(r"^\s*(ARTICLE|SECTION)\s+([IVXLC]+|\d+)\b", re.I),    # ARTICLE IV / SECTION 3
)
# NOTE: deliberately NO "(a)"/"(1)" sub-clause pattern, and a BARE integer with no dot ("72 hours…") is NOT
# a boundary. Sub-clause granularity adds no triage value, and bare-number forms false-fire on spelled-number
# echoes that wrap to a line start (e.g. "...five (5) years...", "72 hours of..."), corrupting boundaries and
# anchors. A boundary number must be multi-level decimal (1.1) OR carry a trailing dot (7.).


@dataclass(frozen=True)
class Anchor:
    page: int                                              # 1-BASED (user-facing "page 4")
    bbox: tuple[float, float, float, float]                # (x0, top, x1, bottom) in PDF points


@dataclass(frozen=True)
class Span:
    text: str
    heading: str | None
    number: str | None
    anchor: Anchor


@dataclass(frozen=True)
class DocumentStructure:
    spans: tuple[Span, ...]
    page_count: int
    degraded: bool
    reason: str                                            # "" | "no_text_layer" | "no_structure_detected"


def parse_number(text: str) -> str | None:
    """Deterministically parse a leading clause number, or None. Pure."""
    for pat in _NUM_PATTERNS:
        m = pat.match(text)
        if m:
            return " ".join(p for p in m.groups() if p).strip()
    return None


@dataclass(frozen=True)
class _Line:
    text: str
    page: int                                              # 1-based
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool


def _lines(pdf) -> list[_Line]:
    """Group words into visual lines, carrying typography (size, bold). Deterministic order: page, then top."""
    out: list[_Line] = []
    for pi, page in enumerate(pdf.pages):
        words = page.extract_words(extra_attrs=["size", "fontname"], use_text_flow=False)
        buckets: list[list[dict]] = []
        for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
            if buckets and abs(w["top"] - buckets[-1][0]["top"]) <= _LINE_TOL:
                buckets[-1].append(w)
            else:
                buckets.append([w])
        for ws in buckets:
            ws = sorted(ws, key=lambda w: w["x0"])
            sizes = [w.get("size", 0.0) for w in ws if w.get("size")]
            out.append(_Line(
                text=" ".join(w["text"] for w in ws),
                page=pi + 1,
                x0=min(w["x0"] for w in ws), top=min(w["top"] for w in ws),
                x1=max(w["x1"] for w in ws), bottom=max(w["bottom"] for w in ws),
                size=statistics.median(sizes) if sizes else 0.0,
                bold=any("bold" in str(w.get("fontname", "")).lower() for w in ws),
            ))
    return out


def _is_boundary(line: _Line, body_size: float) -> bool:
    if parse_number(line.text):
        return True
    short = len(line.text) <= _HEADING_MAX_CHARS
    if short and body_size and line.size >= body_size * _HEADING_SIZE_RATIO:
        return True
    return short and line.bold


class StructureSensor(Protocol):
    def parse(self, path: str) -> DocumentStructure: ...


class LayoutStructureSensor:
    """The V1 deterministic sensor (pdfplumber). Swappable for a VLM sensor later via the protocol."""

    def parse(self, path: str) -> DocumentStructure:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            lines = _lines(pdf)
            page_text = {pi + 1: (pdf.pages[pi].extract_text() or "") for pi in range(page_count)}
            page_box = {pi + 1: (0.0, 0.0, float(pdf.pages[pi].width), float(pdf.pages[pi].height))
                        for pi in range(page_count)}

        if sum(len(ln.text.replace(" ", "")) for ln in lines) < _MIN_TEXT_CHARS:
            return DocumentStructure((), page_count, True, "no_text_layer")   # scanned/image -> never fabricate

        body_size = statistics.median([ln.size for ln in lines if ln.size]) if lines else 0.0
        boundaries = [i for i, ln in enumerate(lines) if _is_boundary(ln, body_size)]

        if not boundaries:                                                    # flat prose -> page-anchored fallback
            spans = tuple(Span(text=page_text[p], heading=None, number=None,
                               anchor=Anchor(p, page_box[p])) for p in sorted(page_text) if page_text[p].strip())
            return DocumentStructure(spans, page_count, True, "no_structure_detected")

        spans = []
        for j, start in enumerate(boundaries):
            end = boundaries[j + 1] if j + 1 < len(boundaries) else len(lines)
            block = lines[start:end]
            head_line = block[0]
            num = parse_number(head_line.text)
            heading = head_line.text[len(num):].lstrip(" .)").strip() if num else head_line.text.strip()
            spans.append(Span(
                text="\n".join(ln.text for ln in block).strip(),
                heading=heading or None,
                number=num,
                anchor=Anchor(head_line.page, (head_line.x0, head_line.top, head_line.x1, head_line.bottom)),
            ))
        return DocumentStructure(tuple(spans), page_count, False, "")
