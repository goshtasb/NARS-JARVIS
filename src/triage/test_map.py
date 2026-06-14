"""Slice 1 / AC1+AC3+AC4 — the Triage Map: degraded maps stay flagged (never fabricated), every anchor
resolves to its real span text (citation integrity), and the pipeline is deterministic + model-free."""
import ast
import os
import time

import pdfplumber

from triage.map import build_triage_map, triage_document
from triage.structure import LayoutStructureSensor

_DIR = os.path.dirname(os.path.abspath(__file__))
_FX = os.path.join(_DIR, "fixtures")


def _norm(s):
    return set(w for w in "".join(c.lower() if c.isalnum() else " " for c in (s or "")).split() if len(w) > 2)


# ── AC1: degraded inputs still yield a flagged map, never a fabricated one ──
def test_scanned_pdf_yields_degraded_empty_map() -> None:
    m = triage_document(os.path.join(_FX, "scanned_no_text.pdf"))
    assert m.degraded and m.degraded_reason == "no_text_layer"
    assert m.entries == ()                                      # zero fabricated entries


def test_flat_pdf_yields_degraded_but_populated_map() -> None:
    m = triage_document(os.path.join(_FX, "flat_no_structure.pdf"))
    assert m.degraded and m.degraded_reason == "no_structure_detected"
    assert len(m.entries) >= 1                                  # best-effort, page-anchored


# ── AC2 (ranking/salience surfaced through the map) ──
def test_map_ranks_salient_clauses_first_and_surfaces_unclassified() -> None:
    m = triage_document(os.path.join(_FX, "nda_born_digital.pdf"))
    assert m.document_type == "NDA"
    assert any("Acme" in p for p in m.parties) and any("Beta" in p for p in m.parties)
    # the high-risk clauses must rank ahead of boilerplate
    salient_headings = [e.heading for e in m.entries if e.salient]
    assert any(h and "indemnif" in h.lower() for h in salient_headings)
    assert any(h and "liquidated" in h.lower() for h in salient_headings)
    # the Counterparts clause is unrecognized -> surfaced as needs_review, never dropped
    counterparts = [e for e in m.entries if (e.heading or "").lower() == "counterparts"]
    assert counterparts and counterparts[0].needs_review and counterparts[0].salient
    # ranking invariant: no non-salient entry precedes a salient one
    flags = [e.salient for e in m.entries]
    assert flags == sorted(flags, reverse=True)


# ── AC3: citation integrity — every anchor re-extracts to its own span text ──
def test_every_entry_anchor_resolves_to_its_span_text() -> None:
    path = os.path.join(_FX, "nda_born_digital.pdf")
    m = triage_document(path)
    with pdfplumber.open(path) as pdf:
        for e in m.entries:
            if not e.heading:
                continue
            page = pdf.pages[e.anchor.page - 1]                 # 1-based -> 0-based
            region = page.crop(e.anchor.bbox).extract_text() or ""
            assert _norm(e.heading) & _norm(region), f"anchor did not land on '{e.heading}': {region!r}"


def test_all_anchor_pages_within_document() -> None:
    m = triage_document(os.path.join(_FX, "nda_born_digital.pdf"))
    assert all(1 <= e.anchor.page <= m.page_count for e in m.entries)


# ── AC4: deterministic, model-free, fast ──
def test_build_is_deterministic() -> None:
    st = LayoutStructureSensor().parse(os.path.join(_FX, "nda_born_digital.pdf"))
    assert build_triage_map(st, "NDA", ("Acme", "Beta")) == build_triage_map(st, "NDA", ("Acme", "Beta"))


def test_pipeline_runs_with_no_llm_env() -> None:
    saved = os.environ.pop("NARS_JARVIS_LLM_GGUF", None)
    try:
        m = triage_document(os.path.join(_FX, "nda_born_digital.pdf"))   # must work with no model wired
        assert m.entries
    finally:
        if saved is not None:
            os.environ["NARS_JARVIS_LLM_GGUF"] = saved


def test_triage_modules_import_no_llm() -> None:
    """AST guard: the triage package must not import any model/LLM/AGPL dependency (CI-enforced determinism)."""
    forbidden = {"language.llm", "language", "llama_cpp", "fitz", "pymupdf"}
    # extract.py is the SINGLE sanctioned model boundary (Slice 2); make_fixtures is dev-only.
    allowed = {"test_", "make_fixtures.py", "extract.py"}
    for fn in os.listdir(_DIR):
        if not fn.endswith(".py") or any(fn.startswith(a) or fn == a for a in allowed):
            continue
        tree = ast.parse(open(os.path.join(_DIR, fn)).read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for n in names:
                assert not any(n == f or n.startswith(f + ".") for f in forbidden), f"{fn} imports {n}"


def test_perf_smoke_40page_under_budget() -> None:
    path = os.path.join(_FX, "nda_40page.pdf")
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
    t0 = time.time()
    m = triage_document(path)
    elapsed = time.time() - t0
    print(f"\n[perf] {pages}-page triage: {elapsed*1000:.0f} ms, {len(m.entries)} entries")
    assert not m.degraded and len(m.entries) > 50
    assert elapsed < 10.0                                       # generous CI bound; real number printed above
