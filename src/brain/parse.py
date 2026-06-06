"""Pure parsers for ONA shell output. Functional Core (S-02) — no I/O, deterministic."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Truth:
    frequency: float
    confidence: float


@dataclass(frozen=True)
class Answer:
    term: str
    truth: Truth | None
    stamp: tuple[int, ...]
    occurrence_time: str  # "eternal" or a timestamp string
    creation_time: int | None


def parse_truth(text: str) -> Truth | None:
    """Extract a Truth from a fragment like 'Truth: frequency=1.0, confidence=0.81'."""
    if "frequency=" not in text or "confidence=" not in text:
        return None
    freq = text.split("frequency=")[1].split(",")[0].split()[0]
    conf = text.split("confidence=")[1].replace(",", " ").split()[0]
    return Truth(float(freq), float(conf))


def parse_stamp(text: str) -> tuple[int, ...]:
    """Extract the evidential stamp from a fragment like 'Stamp=[2,1]'."""
    if "Stamp=[" not in text:
        return ()
    inner = text.split("Stamp=[")[1].split("]")[0]
    return tuple(int(x) for x in inner.split(",") if x.strip())


def _term_of(body: str) -> str:
    head = body
    # Strip every trailing metadata field ONA appends so only the canonical term survives.
    # NB: " Priority" matters for Input/Derived/Revised/Selected lines (Answer lines lack it);
    # without it, terms leaked into L2 polluted as e.g. '<a --> c>. Priority=0.407250'.
    for sep in (" creationTime", " :|:", " occurrenceTime", " Stamp", " Truth", " Priority"):
        head = head.split(sep)[0]
    return head.strip().rstrip(".!?").strip()


def _parse_body(body: str) -> Answer | None:
    if body.startswith("None"):
        return None
    creation: int | None = None
    if "creationTime=" in body:
        try:
            creation = int(body.split("creationTime=")[1].split()[0])
        except ValueError:
            creation = None
    occ = "eternal"
    if "occurrenceTime=" in body:
        occ = body.split("occurrenceTime=")[1].split()[0]
    elif ":|:" in body:
        occ = "now"
    return Answer(
        term=_term_of(body),
        truth=parse_truth(body),
        stamp=parse_stamp(body),
        occurrence_time=occ,
        creation_time=creation,
    )


_PREFIXES = ("Answer:", "Derived:", "Revised:", "Input:", "Selected:")


def parse_answer(line: str) -> Answer | None:
    """Parse an 'Answer:' line into an Answer; None for 'Answer: None.' or non-answer lines."""
    if not line.startswith("Answer:"):
        return None
    return _parse_body(line[len("Answer:"):].strip())


def parse_line(line: str) -> Answer | None:
    """Parse any ONA statement line (Answer/Derived/Revised/Input/Selected) into an Answer."""
    for prefix in _PREFIXES:
        if line.startswith(prefix):
            return _parse_body(line[len(prefix):].strip())
    return None


def input_accepted(lines: list[str]) -> bool:
    """True iff ONA accepted an input belief: it echoes 'Input: <belief>' on success and emits
    'Parsing error:' (no Input echo) on a malformed statement. Used to gate the L2 commit on a
    CONFIRMED L1 ingestion, so a parse-rejected `tell` cannot desync the two tiers."""
    if any("parsing error" in line.lower() for line in lines):
        return False
    return any(line.startswith("Input:") for line in lines)


def canonical_input(lines: list[str]) -> Answer | None:
    """The parsed 'Input:' echo — ONA's NORMALIZED term + truth for the statement it accepted, so
    the L2 write-through mirrors L1 exactly (e.g. '< A --> B > .' -> term '<A --> B>'). None if no
    Input echo is present (the statement was not accepted)."""
    for line in lines:
        if line.startswith("Input:"):
            return parse_line(line)
    return None
