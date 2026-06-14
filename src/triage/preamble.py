"""Slice 1: deterministic page-1 preamble extraction — document type + parties (Functional Core, S-02).

A bounded, regex-only touch over the first page's text (NOT the deterministic layout pass, but still
zero-model and instant). Document type from title keywords; parties from the "by and between X and Y"
recital. Returns (None, ()) on no match — never guesses. If regex party extraction proves insufficient on
real corpora, the documented escalation is a bounded page-1 model touch (Slice 2), not magic here.
"""
from __future__ import annotations

import re

_TYPE_KEYWORDS = (
    ("non-disclosure", "NDA"), ("nondisclosure", "NDA"), ("confidentiality agreement", "NDA"),
    ("master services agreement", "MSA"), ("master service agreement", "MSA"),
    ("data processing agreement", "DPA"), ("data protection agreement", "DPA"),
)
_PARTIES = re.compile(r"by and between\s+(.+?)\s+and\s+(.+?)\s*[\.,\(]", re.I | re.S)


def _clean(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .,")


def extract_preamble(first_page_text: str) -> tuple[str | None, tuple[str, ...]]:
    text = first_page_text or ""
    low = text.lower()
    doc_type = next((label for kw, label in _TYPE_KEYWORDS if kw in low), None)
    m = _PARTIES.search(text)
    parties = tuple(_clean(g) for g in m.groups()) if m else ()
    return doc_type, parties
