"""The bounded web-research loop (ADR-039) — Functional Core over injected effects (S-02).

ADR-035's single pass (search → synthesize from snippets) structurally cannot answer live-data
questions: search engines return *descriptions of pages*, not their contents. This loop gives the
model the missing human move — *click the relevant result and read it*: search → the model picks a
link BY NUMBER from a deterministic menu → fetch it (render-escalated `browse_page`) → that page's
links join the menu → repeat, bounded → synthesize an answer with sources.

The closed-menu pick is the prompt-injection bound: the model NEVER types a URL. It selects an index
into links that deterministic code extracted from pages we already chose to fetch (every fetch is
read-only + SSRF-guarded in the egress subprocess). Hostile page text can nudge *which existing link*
is opened next; it cannot mint a URL, so it cannot encode/exfiltrate data it doesn't already have.

`generate(system, user, max_tokens)` and `perform(action, arg)` are injected — pure-testable with
fakes, no model, no network. Never raises.
"""
from __future__ import annotations

import re
import time
from typing import Callable

Generate = Callable[[str, str, int], str]
Perform = Callable[[str, str], str]

MAX_OPENS = 3        # pages actually fetched per question
MAX_SEARCHES = 2     # web searches per question (incl. the seed)
MAX_STEPS = 8        # decision-loop iterations (backstop over the two caps)
WALL_SECONDS = 120.0  # hard wall-clock bound — research can never stall a turn indefinitely
MENU_CAP = 12        # links offered per decision (7B context discipline)
NOTE_CAP = 1600      # chars kept per fetched page / result list
DECIDE_NOTE_CAP = 800   # chars per note shown at decision steps (full notes go to synthesis)
SYNTH_INPUT_CAP = 8000  # chars of findings fed to the synthesis call (fits the 7B's n_ctx)

_DECIDE_PROMPT = (
    "You are researching the user's question on the web. Decide your SINGLE next move. Reply with "
    "EXACTLY one line and nothing else:\n"
    "OPEN <number>   — read that link from the list (pick the most relevant)\n"
    "SEARCH <query>  — run a DIFFERENT web search (never repeat one already shown in the findings)\n"
    "ANSWER          — you have enough information to answer now\n\n"
    # Few-shot anchoring (the ADR-036/v1.11.1 lesson, applied after a live 3/3 failure where the 7B
    # re-issued the same search instead of opening a link): snippets are pointers, not data.
    "Rule: search-result descriptions are NOT data. If the findings are only result snippets, OPEN "
    "the most relevant link — do not ANSWER from snippets and do not repeat the search.\n"
    "Examples:\n"
    "Findings: result snippets describing forecast pages (no actual temperatures); link 2 is "
    "'Weather Tomorrow | AccuWeather'\n-> OPEN 2\n"
    "Findings: a page you read says 'Tomorrow: high 81F, low 64F, sunny'\n-> ANSWER\n"
    "Findings: results are about Phoenix but the user asked about Denver\n-> SEARCH Denver weather"
)
_SYNTH_PROMPT = (
    "You researched the web and collected the findings below. Answer the user's question concisely "
    "and factually USING ONLY those findings, and name the source site(s). If the findings do not "
    "actually contain the answer, say so plainly — do not guess or invent details."
)

_STEP_RE = re.compile(r"^\s*(OPEN|SEARCH|ANSWER)\b[:\s]*(.*?)\s*$", re.I | re.M)
_RESULT_URL_RE = re.compile(r"^\s+(https?://\S+)\s*$", re.M)
_MENU_LINE_RE = re.compile(r"^\s*\d+\.\s*(.*?)\s+—\s+(https?://\S+)\s*$", re.M)


# ── pure parsers ──
def parse_step(raw: str) -> tuple[str, str]:
    """First decision directive in the model's reply -> ('open', '3') | ('search', q) | ('answer', '').
    Anything unparseable is 'answer' — an undecided model ends the loop, it never free-runs."""
    m = _STEP_RE.search(raw or "")
    if not m:
        return ("answer", "")
    verb, arg = m.group(1).lower(), m.group(2).strip()
    if verb == "open":
        n = re.match(r"\d+", arg)
        return ("open", n.group(0)) if n else ("answer", "")
    if verb == "search":
        return ("search", arg) if arg else ("answer", "")
    return ("answer", "")


def links_from_results(results_text: str) -> list[tuple[str, str]]:
    """(title, url) from `web_lookup`'s numbered text format (title line, snippet, indented URL)."""
    links: list[tuple[str, str]] = []
    lines = (results_text or "").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s+(https?://\S+)\s*$", line)
        if not m:
            continue
        title = ""
        for back in range(i - 1, -1, -1):  # nearest preceding numbered title line
            t = re.match(r"^\d+\.\s*(.+)$", lines[back])
            if t:
                title = t.group(1).strip()
                break
        links.append((title or m.group(1), m.group(1)))
    return links


def split_browse(browse_text: str) -> tuple[str, list[tuple[str, str]]]:
    """`browse_page` output -> (article text, [(anchor text, url)…] from its LINKS section)."""
    text = browse_text or ""
    head, _, tail = text.partition("\n\nLINKS:\n")
    return head.strip(), [(m.group(1).strip(), m.group(2)) for m in _MENU_LINE_RE.finditer(tail)]


# ── the loop (effects injected) ──
def run_research(question: str, seed: list[tuple[str, str]], generate: Generate, perform: Perform,
                 clock: Callable[[], float] = time.monotonic,
                 context: str = "",
                 log: Callable[[str], None] = lambda _m: None) -> tuple[str | None, list[str]]:
    """Research `question` starting from the model's seed directives (web_lookup/read_article).
    `context` is the rendered recent-conversation block (ADR-041/042) so follow-ups like "are you
    sure?" research what they actually refer to; `log` receives a trajectory line per step (wired to
    the daemon log) so field failures are diagnosable. Returns (answer or None, [error strings…])."""
    notes: list[str] = []
    menu: list[tuple[str, str]] = []
    opened: set[str] = set()
    issued: set[str] = set()                     # normalized queries already searched (dup guard)
    errors: list[str] = []
    opens = searches = 0
    deadline = clock() + WALL_SECONDS
    convo = f"{context}\n\n" if context else ""

    def _merge(links: list[tuple[str, str]]) -> None:
        have = {u for _t, u in menu}
        for t, u in links:
            if u not in have and u not in opened and len(menu) < MENU_CAP:
                menu.append((t, u))
                have.add(u)

    def _search(query: str) -> None:
        nonlocal searches
        searches += 1
        issued.add(" ".join((query or question).lower().split()))
        result = perform("web_lookup", query or question)
        if result.lstrip().startswith("[ERROR"):
            errors.append(result)
            log(f"search {query or question!r} -> {result[:80]}")
            return
        notes.append(f"[search: {query or question}]\n{result}"[:NOTE_CAP])
        _merge(links_from_results(result))
        log(f"search {query or question!r} -> {len(menu)} links in menu")

    def _open(url: str, forced: bool = False) -> None:
        nonlocal opens
        opens += 1
        opened.add(url)
        menu[:] = [(t, u) for t, u in menu if u != url]
        result = perform("browse_page", url)
        if result.lstrip().startswith("[ERROR"):
            errors.append(result)
            log(f"open{' (floor)' if forced else ''} {url} -> {result[:80]}")
            return
        article, links = split_browse(result)
        if article:
            notes.append(article[:NOTE_CAP])
        _merge(links)
        log(f"open{' (floor)' if forced else ''} {url} -> {len(article)} chars")

    for name, arg in seed:                       # the model's own [[DO:]] directives kick it off
        if name == "read_article" and arg.startswith(("http://", "https://")):
            if opens < MAX_OPENS:
                _open(arg)
        elif searches < MAX_SEARCHES:
            _search(arg)

    for _step in range(MAX_STEPS):
        can_open, can_search = bool(menu) and opens < MAX_OPENS, searches < MAX_SEARCHES
        if clock() > deadline or not (can_open or can_search):
            break
        shown = "\n---\n".join(n[:DECIDE_NOTE_CAP] for n in notes) or "(nothing useful yet)"
        listing = "\n".join(f"{i}. {t} — {u}" for i, (t, u) in enumerate(menu, 1)) or "(no links)"
        try:
            reply = generate(
                _DECIDE_PROMPT,
                f"{convo}Question: {question}\n\nFindings so far:\n{shown}\n\n"
                f"Links you can OPEN:\n{listing}",
                32)
        except Exception:  # noqa: BLE001 — a model hiccup ends the loop; synthesize what we have
            break
        verb, arg = parse_step(reply)
        chose = False
        if verb == "open" and can_open and arg.isdigit() and 1 <= int(arg) <= len(menu):
            _open(menu[int(arg) - 1][1])
            chose = True
        elif verb == "search" and can_search and " ".join(arg.lower().split()) not in issued:
            _search(arg)                         # a repeat of an issued query is a refusal, not progress
            chose = True
        if not chose:
            # Deterministic floor (ADR-042): the model may not END research in the snippet trap. If it
            # tries to stop (ANSWER / invalid pick / duplicate search) with NOTHING read and links on
            # the table, code opens the top result — proposal/disposal, applied to giving up.
            if opens == 0 and can_open:
                _open(menu[0][1], forced=True)
                continue
            log(f"stop ({verb}) after {opens} opens / {searches} searches")
            break

    if not notes:
        return None, errors                      # everything blocked/empty -> surface the errors
    joined = "\n\n".join(notes)
    try:
        answer = generate(_SYNTH_PROMPT,
                          f"{convo}Question: {question}\n\nFindings:\n{joined[:SYNTH_INPUT_CAP]}",
                          400).strip()
    except Exception:  # noqa: BLE001 — a model hiccup falls back to the raw findings
        answer = ""
    return (answer or joined), []
