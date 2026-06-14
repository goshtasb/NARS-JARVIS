"""Slice 1 / AC1 — StructureSensor returns real structure or a FLAGGED degraded state, never a fabrication.
Reads committed fixtures with pdfplumber only (no matplotlib/PIL at test time)."""
import os

from triage.structure import LayoutStructureSensor, parse_number

_FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _parse(name):
    return LayoutStructureSensor().parse(os.path.join(_FX, name))


# ── AC1 ──
def test_born_digital_yields_spans_with_valid_anchors() -> None:
    st = _parse("nda_born_digital.pdf")
    assert not st.degraded and st.reason == ""
    assert len(st.spans) >= 8                                   # preamble + 8 numbered clauses
    for sp in st.spans:
        assert 1 <= sp.anchor.page <= st.page_count            # anchor pages in range
        x0, top, x1, bottom = sp.anchor.bbox
        assert x1 > x0 and bottom > top                         # a real, non-degenerate box
    headings = {(sp.heading or "").lower() for sp in st.spans}
    assert "indemnification" in headings and "liquidated damages" in headings


def test_scanned_pdf_degrades_no_text_layer() -> None:
    st = _parse("scanned_no_text.pdf")
    assert st.degraded and st.reason == "no_text_layer"
    assert st.spans == ()                                       # zero fabricated spans on an image-only page


def test_flat_text_degrades_to_page_anchored_fallback() -> None:
    st = _parse("flat_no_structure.pdf")
    assert st.degraded and st.reason == "no_structure_detected"
    assert len(st.spans) >= 1                                   # best-effort page-anchored, not empty/silent
    assert all(1 <= sp.anchor.page <= st.page_count for sp in st.spans)


def test_numbered_boundaries_parse_clause_numbers() -> None:
    st = _parse("nda_born_digital.pdf")
    numbers = [sp.number for sp in st.spans if sp.number]
    assert "1" in numbers and "8" in numbers                   # top-level numbering detected


def test_parse_number_is_pure_and_conservative() -> None:
    assert parse_number("7. Indemnification") == "7"
    assert parse_number("1.1 Scope") == "1.1"
    assert parse_number("ARTICLE IV") == "ARTICLE IV"
    assert parse_number("for a period of five (5) years") is None   # spelled-number echo is NOT a boundary
    assert parse_number("The vendor shall notify") is None
