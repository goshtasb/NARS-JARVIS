"""Read-only document work primitives (ADR-032).

The first actions that produce real overnight WORK: extract a local document's text (`read_file_text`,
text-family via stdlib + `.pdf` via lazily-imported `pypdf`) and summarize it (`summarize`, a recursive
**Map-Reduce** — chunk → summarize each chunk → summarize the summaries — so the WHOLE document is
processed, never a silent truncation). Outputs are written only to a `/tmp` scratchpad. All read-only,
local, no network — so they pass the overnight safe-autonomous boundary (`kind="work"`).

Everything here `never raises` — it returns a user-facing string, like `files.find_file`. The `generate`
callable is injected so the Map-Reduce is testable with a fake (no model needed).
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Callable

# Text-family extensions read directly as UTF-8 (decode errors replaced, never raise).
_TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".rtf", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".zsh", ".bash", ".c", ".h", ".cpp", ".rs", ".go",
    ".java", ".rb", ".swift", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".tex",
})
_MAX_BYTES = 5_000_000          # 5 MB hard cap on raw text we ingest (protects memory + the loop)
_MAX_PDF_PAGES = 200            # bound pypdf work on a pathological PDF
_PREVIEW = 280                  # chars of preview returned to the caller / queue result

# Map-Reduce sizing. Conservative char-based chunks (~3.5 chars/token) keep each map call well under
# the model's n_ctx=4096 with room for prompt + output. No tokenizer needed (testable without a model).
_CHUNK_CHARS = 8_000
_MAX_CHUNKS = 40                # bound the number of map calls; over this we summarize a stated subset
_MAP_TOKENS = 256
_REDUCE_TOKENS = 512

_SUMMARY_SYSTEM = ("You are a careful summarizer. Summarize the text faithfully and concisely. "
                   "Do not invent facts not present in the text.")


# ── extraction ──
def read_file_text(path: str) -> str:
    """Extract a local document's text (text-family or PDF). Returns the text, or a user-facing message
    starting with '⚠' on any problem. Never raises."""
    p = (path or "").strip()
    if not p:
        return "⚠ No file path given."
    p = os.path.expanduser(p)
    if not os.path.isfile(p):
        return f"⚠ No such file: {p}"
    try:
        if os.path.getsize(p) > _MAX_BYTES and os.path.splitext(p)[1].lower() != ".pdf":
            return f"⚠ File is too large to read (>{_MAX_BYTES // 1_000_000} MB): {os.path.basename(p)}"
    except OSError as exc:
        return f"⚠ Couldn't stat {os.path.basename(p)}: {exc}"

    ext = os.path.splitext(p)[1].lower()
    if ext == ".pdf":
        return _read_pdf(p)
    if ext in _TEXT_EXTS or ext == "":
        return _read_text(p)
    return f"⚠ Unsupported file type {ext!r} — I can read text files and PDFs (not .docx/.pptx yet)."


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(_MAX_BYTES)
        text = raw.decode("utf-8", errors="replace").strip()
    except OSError as exc:
        return f"⚠ Couldn't read {os.path.basename(path)}: {exc}"
    if not text or text.count("�") > len(text) // 10:    # mostly replacement chars -> binary
        return f"⚠ {os.path.basename(path)} doesn't look like readable text."
    return text


def _read_pdf(path: str) -> str:
    try:
        import pypdf  # lazy: pure-Python, local; absence is reported, not crashed
    except ImportError:
        return "⚠ PDF support needs the 'pypdf' package (pip install pypdf)."
    try:
        reader = pypdf.PdfReader(path)
        pages = reader.pages[:_MAX_PDF_PAGES]
        text = "\n\n".join((pg.extract_text() or "") for pg in pages).strip()
    except Exception as exc:  # noqa: BLE001 — a malformed PDF reports, never crashes the turn
        return f"⚠ Couldn't parse PDF {os.path.basename(path)}: {exc}"
    if not text:
        return (f"⚠ {os.path.basename(path)} has no extractable text "
                "(it may be a scanned/image-only PDF).")
    return text


# ── chunking (pure) ──
def chunk_text(text: str, max_chars: int = _CHUNK_CHARS) -> list[str]:
    """Split text into <=max_chars chunks on paragraph/line boundaries where possible. Pure."""
    text = text or ""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks, buf = [], ""
    for para in re.split(r"(\n\s*\n)", text):           # keep separators so we don't lose structure
        if len(buf) + len(para) > max_chars and buf.strip():
            chunks.append(buf.strip())
            buf = ""
        if len(para) > max_chars:                        # a single huge paragraph -> hard slice
            for i in range(0, len(para), max_chars):
                chunks.append(para[i:i + max_chars].strip())
        else:
            buf += para
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c]


# ── Map-Reduce summarize ──
Generate = Callable[[str, str, int], str]   # (system, user, max_tokens) -> text


def summarize(text: str, generate: Generate, *, max_chunks: int = _MAX_CHUNKS,
              on_step: Callable[[int, int], None] | None = None) -> str:
    """Recursively Map-Reduce-summarize `text` using the injected `generate`. Processes the WHOLE
    document; if it exceeds `max_chunks` sections, summarizes the first `max_chunks` and STATES that
    coverage honestly (never a silent truncation). `on_step(i, n)` (optional) is called before each map
    step so an offloaded worker can stream `[progress] i/N`. Returns the summary, or '⚠ …' on failure."""
    text = (text or "").strip()
    if not text:
        return "⚠ Nothing to summarize (no text extracted)."
    chunks = chunk_text(text)
    coverage = ""
    if len(chunks) > max_chunks:
        coverage = (f"_(Coverage: summarized the first {max_chunks} of {len(chunks)} sections; "
                    f"the document exceeded the bounded overnight limit.)_\n\n")
        chunks = chunks[:max_chunks]

    try:
        summaries = []
        for i, c in enumerate(chunks):
            if on_step is not None:
                on_step(i + 1, len(chunks))         # 1-based "chunk i/N" for the progress stream
            summaries.append(generate(_SUMMARY_SYSTEM, f"Summarize this text:\n\n{c}", _MAP_TOKENS).strip())
    except Exception as exc:  # noqa: BLE001 — a model hiccup reports, never crashes the turn
        return f"⚠ Summarization failed: {exc}"
    summaries = [s for s in summaries if s]
    if not summaries:
        return "⚠ Summarization produced no output."

    # Reduce: collapse the chunk-summaries until they fit in one pass.
    while len(summaries) > 1:
        joined = "\n\n".join(summaries)
        if len(joined) <= _CHUNK_CHARS:
            try:
                final = generate(_SUMMARY_SYSTEM,
                                 f"Combine these section summaries into one summary:\n\n{joined}",
                                 _REDUCE_TOKENS).strip()
            except Exception as exc:  # noqa: BLE001
                return f"⚠ Summarization failed during reduce: {exc}"
            return coverage + (final or joined)
        # too big to combine in one pass -> summarize the summaries in groups, then loop
        try:
            summaries = [generate(_SUMMARY_SYSTEM, f"Summarize this text:\n\n{g}", _MAP_TOKENS).strip()
                         for g in chunk_text(joined)]
        except Exception as exc:  # noqa: BLE001
            return f"⚠ Summarization failed during reduce: {exc}"
        summaries = [s for s in summaries if s]
    return coverage + summaries[0]


# ── scratchpad ──
def scratchpad_dir() -> str:
    """The persisted overnight output dir (created on demand)."""
    d = os.path.join(tempfile.gettempdir(), "jarvis_overnight")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path or "document"))[0]
    return re.sub(r"[^A-Za-z0-9._-]", "_", stem) or "document"


def write_scratchpad(src_path: str, suffix: str, content: str) -> str:
    """Write `content` to <scratchpad>/<stem><suffix> and return the path (best-effort)."""
    out = os.path.join(scratchpad_dir(), _safe_stem(src_path) + suffix)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(content)
    return out


def _preview(text: str) -> str:
    one = " ".join(text.split())
    return one[:_PREVIEW] + ("…" if len(one) > _PREVIEW else "")


# ── action entry points (called by run.perform for kind="work") ──
def do_read_file(path: str) -> str:
    """read_file action: extract text -> scratchpad, return a preview + the saved path."""
    text = read_file_text(path)
    if text.startswith("⚠"):
        return text
    try:
        out = write_scratchpad(path, ".txt", text)
    except OSError as exc:
        return f"⚠ Read {os.path.basename(path)} but couldn't save it: {exc}"
    return f"Read {os.path.basename(path)} ({len(text):,} chars) → {out}\n\n{_preview(text)}"


def do_summarize_file(path: str, generate: Generate | None) -> str:
    """summarize_file action: extract -> Map-Reduce summarize -> scratchpad .summary.md."""
    if generate is None:
        return "⚠ No local model available to summarize (set NARS_JARVIS_LLM_GGUF)."
    text = read_file_text(path)
    if text.startswith("⚠"):
        return text
    summary = summarize(text, generate)
    if summary.startswith("⚠"):
        return summary
    try:
        out = write_scratchpad(path, ".summary.md", summary)
    except OSError as exc:
        return f"⚠ Summarized {os.path.basename(path)} but couldn't save it: {exc}"
    n = len(chunk_text(text))
    return f"Summarized {os.path.basename(path)} → {out} ({n} section{'s' if n != 1 else ''})\n\n{_preview(summary)}"
