"""brain — the persistent symbolic reasoning core (ONA / NARS) as an L1 cache (PRD §6).

Public interface (ADR-001: a Python module's public surface is its `__init__.py` + `__all__`).
"""
from .ona import Brain
from .parse import (
    Answer,
    Truth,
    input_accepted,
    parse_answer,
    parse_line,
    parse_stamp,
    parse_truth,
)

__all__ = ["Brain", "Answer", "Truth", "input_accepted", "parse_answer", "parse_line",
           "parse_stamp", "parse_truth"]
