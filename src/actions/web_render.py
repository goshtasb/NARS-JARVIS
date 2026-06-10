"""On-demand rendered fetch (ADR-039) — the JS escalation tier of the web egress subprocess.

Modern data sites (weather, finance, dashboards) render their content with JavaScript, so a static
urllib GET returns chrome with no data — verified live on weather.com (ADR-039). This module runs a
HEADLESS Chromium via Playwright to get the post-render DOM. It supersedes ADR-034's "no browser"
rejection in one narrow way: that decision rejected a *resident* browser as the primary fetch (RAM);
this is a *transient escalation* — launched only when static extraction came back empty, dead ~seconds
later, and only ever inside the isolated egress subprocess (`web.py`), never the daemon.

Privacy posture: headless, no persistent profile/cookies (a fresh browser context per call), JS runs
sandboxed in Chromium, and only the rendered HTML string crosses back. Lazy import — `web.py` works
fully (static tier) when Playwright isn't installed. Never raises.
"""
from __future__ import annotations

_RENDER_TIMEOUT_MS = 12_000   # hard cap on navigation; a hung site can't stall the research loop
_SETTLE_MS = 1_500            # post-load grace for late XHR data fills (weather widgets etc.)


def render_html(url: str) -> str:
    """Fetch `url` with headless Chromium and return the post-JS DOM HTML, or `[ERROR: …]`.
    The caller (`web.py`) has already passed the URL through the SSRF guard."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "[ERROR: rendered fetch unavailable (playwright not installed)]"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=_RENDER_TIMEOUT_MS, wait_until="domcontentloaded")
                try:  # best-effort settle: many data sites fill content via XHR just after load
                    page.wait_for_load_state("networkidle", timeout=_SETTLE_MS)
                except Exception:  # noqa: BLE001 — busy pages never go idle; proceed with what we have
                    page.wait_for_timeout(_SETTLE_MS)
                return page.content()
            finally:
                browser.close()   # transient by design: no resident browser, ever
    except Exception as exc:  # noqa: BLE001 — any render failure degrades to the static result
        return f"[ERROR: rendered fetch failed: {exc}]"
