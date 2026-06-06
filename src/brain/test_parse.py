"""Unit tests for the pure ONA-output parsers (Functional Core; no subprocess needed)."""
from brain.parse import Truth, canonical_input, parse_answer, parse_line, parse_stamp, parse_truth


def test_truth_and_stamp() -> None:
    assert parse_truth("Truth: frequency=1.000000, confidence=0.810000") == Truth(1.0, 0.81)
    assert parse_stamp("... Stamp=[2,1] ...") == (2, 1)
    assert parse_truth("no truth here") is None
    assert parse_stamp("no stamp here") == ()


def test_parse_answer_deduction() -> None:
    line = (
        "Answer: <a --> c>. creationTime=2 Stamp=[2,1] "
        "Truth: frequency=1.000000, confidence=0.810000"
    )
    a = parse_answer(line)
    assert a is not None
    assert a.term == "<a --> c>"
    assert a.truth == Truth(1.0, 0.81)
    assert a.stamp == (2, 1)
    assert a.creation_time == 2


def test_parse_answer_none() -> None:
    assert parse_answer("Answer: None.") is None
    assert parse_answer("Input: <a --> b>.") is None


def test_parse_line_strips_priority() -> None:
    # Regression: Derived/Input lines carry ' Priority='; the term must NOT include it (else L2
    # gets polluted keys like '<a --> c>. Priority=0.407250').
    line = "Derived: <a --> c>. Priority=0.407250 Stamp=[2,1] Truth: frequency=1.000000, confidence=0.810000"
    ev = parse_line(line)
    assert ev is not None and ev.term == "<a --> c>", ev
    assert ev.truth == Truth(1.0, 0.81)


def test_canonical_input_extracts_normalized_term() -> None:
    lines = ["Input: <A --> B>. Priority=1.000000 Stamp=[1] Truth: frequency=1.000000, confidence=0.900000"]
    echo = canonical_input(lines)
    assert echo is not None and echo.term == "<A --> B>" and echo.truth == Truth(1.0, 0.9)
    assert canonical_input(["Parsing error: ..."]) is None


if __name__ == "__main__":
    test_truth_and_stamp()
    test_parse_answer_deduction()
    test_parse_answer_none()
    test_parse_line_strips_priority()
    test_canonical_input_extracts_normalized_term()
    print("brain/test_parse: OK")
