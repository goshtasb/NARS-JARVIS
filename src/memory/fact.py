"""Fact model + pure helpers for the system-of-record. Functional Core (S-02) — no I/O."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

_TRUTH_RE = re.compile(r"\{\s*([0-9.]+)\s+([0-9.]+)\s*\}\s*$")
_ATOM_RE = re.compile(r"[\w$#^+\-]+")
_TENSES = (":|:", ":/:", ":\\:")


def is_valid_belief(statement: str) -> bool:
    """True if `statement` is a syntactically well-formed Narsese BELIEF (an L2 ingress gate).

    Structural — NOT a full NAL grammar (ONA stays the authority): requires balanced <> () [] {}
    delimiters, a non-empty term, '.' belief punctuation (rejects '!' goals and '?' questions), and
    a valid optional '{freq conf}' truth with both values in [0,1]. Pure. Rejects e.g. 'garbage((('.
    """
    s = statement.strip()
    if not s:
        return False
    # Balance only ( ) [ ] { } — NOT < >, which appear inside Narsese copulas (-->, <->, ==>).
    pairs = {"(": ")", "[": "]", "{": "}"}
    closers = set(pairs.values())
    stack: list[str] = []
    for ch in s:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in closers and (not stack or stack.pop() != ch):
            return False
    if stack:
        return False
    body = s
    truth = _TRUTH_RE.search(s)
    if truth:
        freq, conf = float(truth.group(1)), float(truth.group(2))
        if not (0.0 <= freq <= 1.0 and 0.0 <= conf <= 1.0):
            return False
        body = s[: truth.start()].strip()
    for tense in _TENSES:
        if body.endswith(tense):
            body = body[: -len(tense)].strip()
    if body.endswith(("!", "?")) or not body.endswith("."):
        return False
    term = body[:-1].strip()
    if not term:
        return False
    # A statement/compound must be bracket-closed; otherwise it must be a bare atom.
    shape = {"<": ">", "(": ")", "[": "]"}.get(term[0])
    return term.endswith(shape) if shape else bool(_ATOM_RE.fullmatch(term))


@dataclass(frozen=True)
class Fact:
    narsese: str  # bare term, e.g. "<tim --> duck>"
    english: str | None
    frequency: float
    confidence: float
    embedding: tuple[float, ...] | None
    pinned: bool
    priority_tier: int
    use_count: int
    created_at: float
    updated_at: float
    last_used: float


def statement_term(statement: str) -> str:
    """Bare term (drop punctuation + truth) from a compiled statement. Pure.

    '<tim --> duck>.'            -> '<tim --> duck>'
    '<tim --> [hungry]>. {0 0.9}' -> '<tim --> [hungry]>'
    """
    return statement.split(". ")[0].rstrip(".").strip()


def to_statement(term: str, frequency: float, confidence: float) -> str:
    """Reconstruct a Narsese belief with explicit truth, for faithful L2->L1 reload. Pure."""
    return f"{term}. {{{frequency:.6f} {confidence:.6f}}}"


def statement_truth(statement: str) -> tuple[float, float]:
    """Read an explicit '{f c}' truth from a statement, else the default belief truth. Pure.

    '<x --> y>. {0.0 0.9}' -> (0.0, 0.9);  '<x --> y>.' -> (1.0, 0.9)
    """
    if "{" in statement:
        parts = statement.split("{", 1)[1].split("}", 1)[0].split()
        return float(parts[0]), float(parts[1])
    return 1.0, 0.9


def pack_embedding(vec: list[float] | None) -> bytes | None:
    return None if vec is None else struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes | None) -> tuple[float, ...] | None:
    return None if blob is None else struct.unpack(f"{len(blob) // 4}f", blob)
