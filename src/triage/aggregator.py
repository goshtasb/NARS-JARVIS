"""Slice 2 deterministic deviation engine: the per-kind corpus baseline + the deviation driver.

Builds 'your standard' as PER-KIND cohorts partitioned by (clause_type, role, kind) — never a blended mean
(blending business-days with calendar-hours would re-introduce the false equivalence the comparator
eliminates). Qualitative parameters are counted separately, never measured. Model-free (AST-guarded).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from triage.parameter import Parameter, ParameterKind, Verdict, compare
from triage.structure import Anchor


@dataclass(frozen=True)
class Cohort:
    kind: str                          # ParameterKind.value
    n: int
    median: float                      # representative canonical (hours for durations)
    lo: float
    hi: float


@dataclass(frozen=True)
class CohortSet:
    clause_type: str
    role: str
    cohorts: tuple[Cohort, ...]        # one per kind — NEVER blended
    qualitative_count: int

    def for_kind(self, kind: str) -> Cohort | None:
        return next((c for c in self.cohorts if c.kind == kind), None)

    def dominant(self) -> Cohort | None:
        return max(self.cohorts, key=lambda c: c.n) if self.cohorts else None


@dataclass(frozen=True)
class Finding:
    param: Parameter
    verdict: Verdict | None            # None == new to corpus (no cohort to compare against)
    cohort: Cohort | None


def _representative(row: dict) -> float | None:
    lo, hi = row.get("canon_lo"), row.get("canon_hi")
    if lo is None:
        return None
    return (lo + hi) / 2 if hi is not None else lo     # interval -> midpoint; open/exact -> lo


def build_baseline(rows: list[dict]) -> dict[tuple[str, str], CohortSet]:
    """Partition rows into per-(clause_type, role, kind) cohorts. Qualitative counted separately."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["clause_type"], r["role"])].append(r)
    out: dict[tuple[str, str], CohortSet] = {}
    for (ctype, role), rs in groups.items():
        by_kind: dict[str, list[float]] = defaultdict(list)
        qual = 0
        for r in rs:
            if r.get("is_qualitative") or r.get("kind") == ParameterKind.QUALITATIVE.value:
                qual += 1
                continue
            rep = _representative(r)
            if rep is not None:
                by_kind[r["kind"]].append(rep)
        cohorts = tuple(Cohort(k, len(vs), statistics.median(vs), min(vs), max(vs))
                        for k, vs in sorted(by_kind.items()))
        out[(ctype, role)] = CohortSet(ctype, role, cohorts, qual)
    return out


def _synthetic_std(cohort: Cohort) -> Parameter:
    """A point-Parameter at the cohort median, for comparison only (canonical drives duration comparison)."""
    return Parameter(role="", clause_type="", raw_quote="", kind=ParameterKind(cohort.kind),
                     value=cohort.median, unit="", canon_lo=cohort.median, canon_hi=cohort.median,
                     is_qualitative=False, anchor=Anchor(0, (0.0, 0.0, 0.0, 0.0)))


def find_deviations(new_params: list[Parameter], baseline: dict[tuple[str, str], CohortSet]) -> list[Finding]:
    """Compare each new parameter against its same-kind cohort (else the dominant cohort, via the partial
    order). No cohort -> 'new to corpus' (verdict None)."""
    findings: list[Finding] = []
    for p in new_params:
        cs = baseline.get((p.clause_type, p.role))
        if cs is None or not cs.cohorts:
            findings.append(Finding(p, None, None))
            continue
        cohort = cs.for_kind(p.kind.value) or cs.dominant()
        findings.append(Finding(p, compare(p, _synthetic_std(cohort)), cohort))
    return findings
