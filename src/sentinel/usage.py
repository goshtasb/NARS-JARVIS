"""Passive-usage aggregation (ADR-050 slice) — Functional Core (S-02). Turns the content-blind
`usage_events` log (one row per foreground app switch: bundle + coarse category + timestamp) into a
human "What I've noticed about your computer use" summary for the Cognitive Identity.

Pure given the events list + `now` — no I/O, no model, no NARS. It only ever sees app IDENTITY and
TIME (never titles/urls/content), so the summary is, by construction, privacy-preserving.
"""
from __future__ import annotations

import time

# Friendly names for common apps (bundle ids are often opaque, e.g. Cursor = com.todesktop.<hash>).
# Best-effort: a known prefix wins; otherwise a cleaned bundle component is used.
_KNOWN: dict[str, str] = {
    "com.apple.safari": "Safari", "com.google.chrome": "Chrome", "org.mozilla.firefox": "Firefox",
    "com.brave.browser": "Brave", "company.thebrowser": "Arc",
    "com.tinyspeck.slackmacgap": "Slack", "com.microsoft.teams": "Teams",
    "com.todesktop": "Cursor", "com.microsoft.vscode": "VS Code",
    "com.apple.mail": "Mail", "com.apple.dt.xcode": "Xcode", "com.apple.terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm", "dev.warp": "Warp",
    "com.spotify.client": "Spotify", "com.apple.music": "Music", "com.apple.finder": "Finder",
    "com.hnc.discord": "Discord", "com.apple.systempreferences": "System Settings",
    "net.whatsapp.whatsapp": "WhatsApp", "org.whispersystems.signal-desktop": "Signal",
    "com.openai.chat": "ChatGPT", "com.anthropic.claudefordesktop": "Claude",
    "notion.id": "Notion", "com.figma.desktop": "Figma", "us.zoom.xos": "Zoom",
}
_DROP = {"com", "org", "net", "io", "co", "app", "www", "apple", "desktop", "macos", "client", "inc"}

# System / background processes that aren't "apps you use" — filtered from the mirror so it reflects
# real work, not auth prompts and window chrome. Matched as a bundle-id substring (case-insensitive).
_SYSTEM_SKIP = (
    "securityagent", "loginwindow", "windowserver", "controlcenter", "notificationcenter",
    "spotlight", "dock", "systemuiserver", "coreservicesuiagent", "universalcontrol",
    "screensaver", "wallpaper", "talagent", "tipsd", "askpermissionui",
)


def _is_system(bundle: str) -> bool:
    bl = (bundle or "").lower()
    return any(tok in bl for tok in _SYSTEM_SKIP)


def app_name(bundle: str) -> str:
    """Best-effort friendly name from a bundle id. Pure."""
    bl = (bundle or "").lower()
    for prefix, name in _KNOWN.items():
        if bl.startswith(prefix):
            return name
    parts = [p for p in bundle.split(".") if p.lower() not in _DROP and p.isalpha()]
    return parts[-1].title() if parts else (bundle or "an app")


def _dur(seconds: float) -> str:
    m = int(seconds // 60)
    if m >= 60:
        return f"{m // 60}h {m % 60}m" if m % 60 else f"{m // 60}h"
    return f"{m}m" if m else "<1m"


def _span(hours: float) -> str:
    if hours >= 48:
        return f"{int(hours // 24)} days"
    return f"{int(hours)} hours" if hours >= 1.5 else "the last while"


def _hour12(h: int) -> str:
    ap = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {ap}"


def summarize_usage(events: list[dict], now: float) -> str:
    """[{bundle, bucket, created_at}…] + now -> a 'What I've noticed' summary. '' when no data."""
    events = sorted([e for e in events if e.get("created_at") is not None
                     and not _is_system(e.get("bundle", ""))], key=lambda e: e["created_at"])
    if not events:
        return ""
    span_h = (now - events[0]["created_at"]) / 3600
    dwell_app: dict[str, float] = {}
    dwell_cat: dict[str, float] = {}
    hours: dict[int, int] = {}
    for i, e in enumerate(events):
        end = events[i + 1]["created_at"] if i + 1 < len(events) else now
        d = max(0.0, min(end - e["created_at"], 1800.0))     # cap one dwell at 30m (idle/away)
        app, cat = app_name(e["bundle"]), (e["bucket"] or "other")
        dwell_app[app] = dwell_app.get(app, 0.0) + d
        dwell_cat[cat] = dwell_cat.get(cat, 0.0) + d
        hours[time.localtime(e["created_at"]).tm_hour] = hours.get(time.localtime(e["created_at"]).tm_hour, 0) + 1
    top_app = [(a, s) for a, s in sorted(dwell_app.items(), key=lambda x: -x[1]) if s > 0][:4]
    top_cat = [(c, s) for c, s in sorted(dwell_cat.items(), key=lambda x: -x[1]) if s > 0][:3]
    busiest = max(hours.items(), key=lambda x: x[1])[0] if hours else None
    lines = [f"What I've noticed about your computer use ({_span(span_h)}, {len(events)} app switches):"]
    if top_app:
        lines.append("- Most of your time: " + ", ".join(f"{a} ({_dur(s)})" for a, s in top_app))
    if top_cat:
        lines.append("- By kind: " + ", ".join(f"{c} ({_dur(s)})" for c, s in top_cat))
    if busiest is not None:
        lines.append(f"- Busiest around: {_hour12(busiest)}")
    lines.append("(Learned passively from which app is in front — never your screen contents.)")
    return "\n".join(lines)
