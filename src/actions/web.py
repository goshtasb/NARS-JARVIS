#!/usr/bin/env python3
"""Keyless, read-only web egress (ADR-034) — runs as an ISOLATED SUBPROCESS, never in the daemon.

The persistent brain process stays network-free; this short-lived child is the ONLY thing that touches
the internet. It does a lightweight stdlib `urllib` GET (no browser, ~15 MB footprint — that's why we
rejected Playwright on a RAM-constrained box), then parses locally with readability-lxml + BeautifulSoup.

Hardened: an SSRF guard (only http(s) to public hosts), a bounded read + Content-Type guard (a 50 MB or
non-HTML payload can never blow up memory), bounded retry/backoff on 403/429 with a same-provider
alternate, and explicit `[ERROR: …]` strings on failure — it NEVER raises and NEVER fakes a blank result.

Scraped text is treated as hostile/untrusted: it only ever becomes a context string for the model; the
closed action catalog + consent gate + read-only-overnight boundary contain any prompt injection.

CLI: `python web.py search "<query>"`  |  `python web.py read "<url>"`  -> result string on stdout.
"""
from __future__ import annotations

import ipaddress
import json
import random
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Use the macOS Keychain for TLS verification (ADR-034). Python.org Python ignores the system trust
# store, so on networks with a TLS-intercepting proxy (corporate self-signed root) urllib fails with
# CERTIFICATE_VERIFY_FAILED even though the browser works. truststore delegates to the OS trust store —
# it trusts exactly what the system trusts (incl. a proxy root), WITHOUT disabling verification. Runs
# only in this subprocess; best-effort (falls back to the default context if unavailable).
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

_TIMEOUT = 8.0
_MAX_FETCH_BYTES = 3_000_000        # hard cap: never pull a 50 MB payload into the subprocess
_OUTPUT_CAP = 12_000                # article text handed to the 7B (protects its context window)
_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
)


# ── safety ──
def is_ssrf_safe(url: str) -> bool:
    """True iff `url` is an http(s) URL to a PUBLIC host. Blocks loopback/private/link-local and any
    non-http scheme (file:, etc.) — the model can emit any URL and this runs unattended."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))   # resolves names too
        return not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved)
    except Exception:  # noqa: BLE001 — unresolvable / malformed -> unsafe
        return False


# ── fetch (bounded, retried) ──
def _fetch(url: str, alternatives: list[str] | None = None) -> str:
    """GET `url` (then any alternates), returning HTML text or an `[ERROR: …]` string. Bounded read +
    Content-Type guard + retry/backoff on 403/429. Never raises."""
    for current in [url, *(alternatives or [])]:
        if not is_ssrf_safe(current):
            return "[ERROR: blocked by SSRF guard (non-public or non-http URL)]"
        for attempt in range(2):
            try:
                req = urllib.request.Request(current, headers={"User-Agent": random.choice(_USER_AGENTS)})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if "html" not in ctype and "text" not in ctype:
                        return "[ERROR: not an HTML page (use read_file for PDFs/other documents)]"
                    clen = resp.headers.get("Content-Length")
                    if clen and clen.isdigit() and int(clen) > _MAX_FETCH_BYTES:
                        return "[ERROR: page too large to read safely]"
                    return resp.read(_MAX_FETCH_BYTES).decode("utf-8", errors="ignore")   # capped read
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    time.sleep(1.0 + random.random())     # jittered backoff, then retry / next alternate
                    continue
                return f"[ERROR: server responded {exc.code}]"
            except Exception as exc:  # noqa: BLE001
                return f"[ERROR: network request failed: {exc}]"
    return "[ERROR: target rate-limited or blocked after retries]"


# ── parsing (pure — testable on fixture HTML, no network) ──
def _decode_ddg(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<real-url>. Unwrap to the real URL."""
    if "uddg=" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        if q.get("uddg"):
            return urllib.parse.unquote(q["uddg"][0])
    return ("https:" + href) if href.startswith("//") else href


def parse_ddg(html: str) -> str:
    """Top-5 results from a DuckDuckGo text-endpoint page -> JSON [{title,url,snippet}], or [ERROR…]."""
    if html.startswith("[ERROR:"):
        return html
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for a in soup.select("a.result__a")[:5]:                         # html.duckduckgo.com/html/ layout
        body = a.find_parent(class_="result__body") or a.parent
        snip = body.select_one(".result__snippet") if body else None
        results.append({"title": a.get_text(" ", strip=True),
                        "url": _decode_ddg(a.get("href", "")),
                        "snippet": snip.get_text(" ", strip=True) if snip else ""})
    if not results:                                                  # lite.duckduckgo.com/lite/ layout
        for a in soup.select("a.result-link")[:5]:
            results.append({"title": a.get_text(" ", strip=True),
                            "url": _decode_ddg(a.get("href", "")), "snippet": ""})
    if not results:
        return "[ERROR: DuckDuckGo returned no parseable results (markup may have changed)]"
    return json.dumps(results, indent=2)


def extract_article(html: str, url: str) -> str:
    """Main article text via readability-lxml (drops nav/ads/chrome). Returns text or [ERROR…]."""
    if html.startswith("[ERROR:"):
        return html
    try:
        from bs4 import BeautifulSoup
        from readability import Document
        doc = Document(html)
        title = doc.title()
        text = BeautifulSoup(doc.summary(), "html.parser").get_text("\n")
        body = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
        if not body:
            return "[ERROR: no article text could be extracted from this page]"
        return f"Title: {title}\nSource: {url}\n\n{body}"[:_OUTPUT_CAP]
    except Exception as exc:  # noqa: BLE001
        return f"[ERROR: article extraction failed: {exc}]"


def _main(argv: list[str]) -> str:
    if len(argv) < 3:
        return "[ERROR: usage: web.py <search|read> <query|url>]"
    mode, arg = argv[1], argv[2]
    if mode == "search":
        q = urllib.parse.quote_plus(arg)
        return parse_ddg(_fetch(f"https://html.duckduckgo.com/html/?q={q}",
                                [f"https://lite.duckduckgo.com/lite/?q={q}"]))
    if mode == "read":
        return extract_article(_fetch(arg), arg)
    return f"[ERROR: unknown mode {mode!r}]"


if __name__ == "__main__":
    print(_main(sys.argv))
