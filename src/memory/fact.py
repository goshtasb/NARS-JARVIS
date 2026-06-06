"""Fact model + pure helpers for the system-of-record. Functional Core (S-02) — no I/O."""
from __future__ import annotations

import struct
from dataclasses import dataclass


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
