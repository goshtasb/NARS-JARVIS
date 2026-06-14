"""Generate the reproducible Slice-1 test PDFs (run once; the .pdf outputs are committed).

`python -m triage.make_fixtures` writes into triage/fixtures/. The TEST SUITE only ever READS these with
pdfplumber — it never imports matplotlib/PIL — so the runtime/CI dependency stays pdfplumber-only. matplotlib
(BSD) emits an extractable text layer; PIL (HPND) emits an image-only page (no text layer) for the
scanned-document degraded case. Deterministic: no randomness, fixed content.
"""
from __future__ import annotations

import os

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# (number, heading, body) — authored so every AC2 case has a deterministic target.
_NDA_CLAUSES = [
    ("1", "Confidentiality",
     "The Receiving Party shall hold all Confidential Information in strict confidence "
     "for a period of five (5) years from the date of disclosure."),
    ("2", "Term and Termination",
     "This Agreement commences on the Effective Date and continues for two (2) years. "
     "Either party may terminate this Agreement upon thirty (30) days written notice."),
    ("3", "Indemnification",
     "Supplier shall indemnify and hold harmless the Buyer against any third-party claims "
     "arising from Supplier's gross negligence or willful misconduct."),
    ("4", "Breach Notification",
     "Vendor shall notify Customer within seventy-two (72) hours of any data breach "
     "affecting personal data processed under this Agreement."),
    ("5", "Liquidated Damages",   # heading says one thing; FUNCTION is a liability cap (the crucible case)
     "In no event shall either party's aggregate liability exceed the fees paid in the prior "
     "twelve months. The parties agree such liquidated damages are a reasonable estimate."),
    ("6", "Governing Law",
     "This Agreement is governed by and construed in accordance with the laws of the State of Delaware."),
    ("7", "Miscellaneous",        # data-protection buried under a non-descriptive heading (crucible case)
     "Provider shall process all personal data and maintain data security; any sub-processor "
     "must be approved in writing before any data breach exposure can occur."),
    ("8", "Counterparts",         # matches NO lexicon -> UNCLASSIFIED target
     "This Agreement may be executed in counterparts, each of which is deemed an original."),
]

_NDA_PREAMBLE = [
    ("MUTUAL NON-DISCLOSURE AGREEMENT", 15, True),
    ("This Agreement is entered into by and between Acme Corporation and Beta Industries, LLC.", 11, False),
    ("", 11, False),
]


def _lines_from_clauses(clauses):
    lines = list(_NDA_PREAMBLE)
    for num, heading, body in clauses:
        lines.append((f"{num}. {heading}", 13, True))     # numbered + bold + larger -> boundary by 2 signals
        lines.append((body, 11, False))
        lines.append(("", 11, False))
    return lines


def _write_text_pdf(path, lines):
    import matplotlib
    matplotlib.use("pdf")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.5, 11)); y = 0.95
        for text, size, bold in lines:
            if y < 0.07:                                   # paginate
                pdf.savefig(fig); plt.close(fig); fig = plt.figure(figsize=(8.5, 11)); y = 0.95
            if text:
                fig.text(0.08, y, text, fontsize=size, fontweight="bold" if bold else "normal",
                         family="serif", va="top", wrap=True)
            y -= (size + 7) / 792.0
        pdf.savefig(fig); plt.close(fig)


def _write_image_only_pdf(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (850, 1100), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, 790, 240], outline="black", width=3)   # a "scanned" page: pixels only, NO text layer
    d.line([60, 320, 790, 320], fill="black", width=2)
    img.save(path, "PDF", resolution=100.0)


def main() -> int:
    os.makedirs(_DIR, exist_ok=True)
    _write_text_pdf(os.path.join(_DIR, "nda_born_digital.pdf"), _lines_from_clauses(_NDA_CLAUSES))
    _write_text_pdf(os.path.join(_DIR, "nda_40page.pdf"), _lines_from_clauses(_NDA_CLAUSES * 60))  # ~40pp perf
    _write_text_pdf(os.path.join(_DIR, "flat_no_structure.pdf"), [
        ("This document contains continuous prose with no numbering, no headings, and a single uniform "
         "font throughout, so that the structure sensor can detect no reliable clause boundaries.", 11, False),
        ("It exists solely to exercise the no_structure_detected degraded fallback path, which must still "
         "produce a page-anchored best-effort map rather than an empty or fabricated one.", 11, False),
    ])
    _write_image_only_pdf(os.path.join(_DIR, "scanned_no_text.pdf"))
    print(f"wrote fixtures -> {_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
