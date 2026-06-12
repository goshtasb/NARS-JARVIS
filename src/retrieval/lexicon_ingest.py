"""Wire ingest -> lexicon (ADR-056 / Gate 2). The bridge that turns every committed belief into a
populated lexicon, so Stage 1 has a living namespace to resolve against.

- TERMS: pulled from the committed canonical Narsese term (the normalized form ONA echoed). Atoms are
  already lowercased/sanitized by `atom()` at ingest, so the lexicon only ever sees canonical terms —
  never raw telemetry frame strings (the Telemetry-Stream-Isolation guardrail is satisfied by
  construction: a raw Sentinel frame never becomes a Narsese atom).
- ALIASES: surface->canonical pairs the extractor explicitly yielded (e.g. `SOL` -> `solana`). These
  cannot be inferred from the canonical term alone, so the extractor must emit them.

Pure term-parsing core + thin store-writing shell.
"""
from __future__ import annotations

import re

# Atoms in committed Narsese are lowercased identifiers (atom() output). Copulas (-->, *, &&) carry no
# letters; truth values {f c} are stripped first so their digits don't leak in as bogus terms.
_TRUTH_RE = re.compile(r"\{[^}]*\}")
_ATOM_RE = re.compile(r"[a-z][a-z0-9_]*")


def terms_in_narsese(narsese: str) -> list[str]:
    """Extract the atomic terms from a committed Narsese statement (order-preserving, de-duped). Pure."""
    body = _TRUTH_RE.sub(" ", narsese or "")
    out: list[str] = []
    seen: set[str] = set()
    for atom in _ATOM_RE.findall(body):
        if atom not in seen:
            seen.add(atom)
            out.append(atom)
    return out


def record_narsese_terms(lexicon, narsese: str, *, now: float) -> list[str]:
    """Register every atom of a committed statement into the lexicon. Returns what was recorded."""
    terms = terms_in_narsese(narsese)
    for term in terms:
        lexicon.record_term(term, now=now)
    return terms


def record_alias_pairs(lexicon, pairs, *, now: float) -> int:
    """Record extractor-yielded surface->canonical pairs. Each pair is {surface, canonical}; malformed
    or empty entries are skipped. Returns the count actually written."""
    written = 0
    for p in pairs or []:
        if not isinstance(p, dict):
            continue
        surface = str(p.get("surface", "")).strip()
        canonical = str(p.get("canonical", "")).strip()
        if not surface or not canonical:
            continue
        lexicon.record_alias(surface, canonical, now=now)
        written += 1
    return written
