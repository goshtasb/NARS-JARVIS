"""ADR-032 work primitives: read-only extraction + the WHOLE-document Map-Reduce summarizer (never a
silent truncator). The `generate` callable is faked so these run without a model."""
import os
import tempfile

from actions import documents


def _tmp(text: str, suffix: str = ".txt") -> str:
    path = tempfile.mktemp(suffix=suffix)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ── read_file_text ──
def test_read_text_file() -> None:
    p = _tmp("hello\nworld")
    assert documents.read_file_text(p) == "hello\nworld"


def test_read_missing_and_unsupported_never_raise() -> None:
    assert documents.read_file_text("/no/such/file.txt").startswith("⚠ No such file")
    assert documents.read_file_text("").startswith("⚠ No file path")
    p = _tmp("x", suffix=".docx")
    assert "Unsupported" in documents.read_file_text(p)        # .docx not supported in v1


def test_read_pdf_path_runs_and_reports_no_text_for_image_only() -> None:
    # Exercises the pypdf branch. A blank page has no extractable text -> honest message, not fabrication.
    import pypdf
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    p = tempfile.mktemp(suffix=".pdf")
    with open(p, "wb") as fh:
        w.write(fh)
    out = documents.read_file_text(p)
    assert out.startswith("⚠") and "image-only" in out        # no text, said so (didn't invent any)


def test_read_binary_is_reported_not_returned() -> None:
    p = tempfile.mktemp(suffix=".txt")
    with open(p, "wb") as fh:
        fh.write(bytes(range(256)) * 50)                       # mostly non-text bytes
    assert documents.read_file_text(p).startswith("⚠")


# ── chunk_text (pure) ──
def test_chunk_text_splits_and_preserves_everything() -> None:
    text = "\n\n".join(f"para {i} " + "x" * 500 for i in range(20))
    chunks = documents.chunk_text(text, max_chars=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    joined = " ".join(chunks)
    for i in range(20):                                        # nothing dropped
        assert f"para {i}" in joined


# ── Map-Reduce summarize ──
def _counting_generate():
    seen = []
    def gen(system, user, max_tokens):
        seen.append(user)
        return f"S{len(seen)}"
    return gen, seen


def test_summarize_maps_every_chunk_then_reduces() -> None:
    text = "\n\n".join(f"section {i} " + "y" * 4000 for i in range(6))   # ~24k chars -> multiple chunks
    gen, seen = _counting_generate()
    out = documents.summarize(text, gen)
    n_chunks = len(documents.chunk_text(text))
    assert n_chunks > 1
    # every chunk was mapped (its text appears in some generate call), then a reduce pass ran
    map_calls = [u for u in seen if u.startswith("Summarize this text:")]
    assert len(map_calls) >= n_chunks
    assert any(u.startswith("Combine these section summaries") for u in seen)   # reduce happened
    assert not out.startswith("⚠")


def test_summarize_states_coverage_when_over_cap() -> None:
    # Force the cap: many chunks, tiny max_chunks -> must say what it covered (no silent truncation).
    text = "\n\n".join(f"p{i} " + "z" * 4000 for i in range(10))
    gen, _ = _counting_generate()
    out = documents.summarize(text, gen, max_chunks=3)
    assert "Coverage" in out and "of" in out                  # explicit coverage statement
    assert "3" in out                                         # the cap it actually processed


def test_summarize_empty_is_graceful() -> None:
    gen, _ = _counting_generate()
    assert documents.summarize("   ", gen).startswith("⚠")


# ── scratchpad-writing entry points ──
def test_do_read_file_writes_scratchpad_and_previews() -> None:
    p = _tmp("the quick brown fox " * 50)
    out = documents.do_read_file(p)
    assert "→" in out and os.path.isfile(out.split("→", 1)[1].split("\n", 1)[0].strip())


def test_do_summarize_file_without_model_is_honest() -> None:
    p = _tmp("content")
    assert documents.do_summarize_file(p, None).startswith("⚠ No local model")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("actions/test_documents: OK")
