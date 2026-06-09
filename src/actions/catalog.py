"""The Conversational-Action Catalog (ADR-019). Functional Core (S-02) — pure, model-free, CLOSED.

The LLM proposes an action via a `[[DO: <action>]]` directive; this catalog *disposes* it. There is
no generative execution path: the ONLY actions JARVIS can take are the ones enumerated here, each
bound to a fixed argv template (or a diagnostics key). An unknown action name resolves to `None` and
is refused by `run.perform`. Parameterized actions (`open_app`/`open_url`/`web_search`) carry the
argument through an *allowlist* validator before it is placed as a single argv element — so a
hallucinated path (`/bin/bash`), flag (`--args`), or scheme (`file://`) can never reach the spawn.

Pure: this module builds argv lists and validates args; it never spawns. `run.perform` is the shell.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class Action:
    """One catalog entry. `kind` is 'argv' (runs a vetted command) or 'diag' (a read-only report).
    `takes_arg` marks the parameterized actions whose argument must pass a validator. `confirm` marks
    destructive/irreversible actions that must clear the interactive consent gate (ADR-020) before
    they run — the runner returns a ConsentSpec instead of executing."""
    name: str
    label: str            # short human description — used in the prompt list and the "(Done: …)" ack
    kind: str             # "argv" | "diag"
    takes_arg: bool = False
    confirm: bool = False


# Closed registry, in display order. Argv templates for the non-parameterized actions live in
# `_STATIC_ARGV`; the parameterized ones are built (and sanitized) in `argv_for`.
_ACTIONS: tuple[Action, ...] = (
    Action("report_system", "report CPU / memory / disk / battery and flag anomalies", "diag"),
    Action("dark_mode", "toggle dark / light mode", "argv"),
    Action("volume_up", "turn the volume up", "argv"),
    Action("volume_down", "turn the volume down", "argv"),
    Action("mute", "mute system audio", "argv"),
    Action("unmute", "unmute system audio", "argv"),
    Action("lock_screen", "lock the screen (sleep the display)", "argv"),
    Action("open_app", "open an application by name", "argv", takes_arg=True),
    Action("open_url", "open a web address in the browser", "argv", takes_arg=True),
    Action("web_search", "search the web for a query", "argv", takes_arg=True),
    # Destructive / irreversible -> gated behind interactive consent (ADR-020).
    Action("empty_trash", "empty the Trash (permanently delete its contents)", "argv", confirm=True),
)

_REGISTRY: dict[str, Action] = {a.name: a for a in _ACTIONS}

# Fixed argv for the no-argument commands. Each is a list/tuple of literal strings handed straight to
# safespawn (never a shell string) — the osascript scripts are single argv elements, not interpolated.
_STATIC_ARGV: dict[str, tuple[str, ...]] = {
    "dark_mode": ("osascript", "-e",
                  'tell application "System Events" to tell appearance preferences '
                  'to set dark mode to not dark mode'),
    "volume_up": ("osascript", "-e",
                  "set volume output volume ((output volume of (get volume settings)) + 10)"),
    "volume_down": ("osascript", "-e",
                    "set volume output volume ((output volume of (get volume settings)) - 10)"),
    "mute": ("osascript", "-e", "set volume output muted true"),
    "unmute": ("osascript", "-e", "set volume output muted false"),
    "lock_screen": ("pmset", "displaysleepnow"),
    "empty_trash": ("osascript", "-e", 'tell application "Finder" to empty trash'),
}

# Allowlist validators (defense in depth — argv-only spawn already kills word-splitting):
#  - app name: must START alphanumeric (rejects leading-'-' flag injection like --args/-W), contain
#    no '/' and no '..' (rejects absolute/relative path execution like /bin/bash), 1-64 chars from a
#    benign set. So "Safari"/"Google Chrome"/"Visual Studio Code" pass; everything else is refused.
#  - url: must be http(s) (rejects file:// and bare local paths) and contain no whitespace.
_APP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+-]{0,63}$")
_HTTP_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)


def resolve(name: str) -> Action | None:
    """The single lookup point: a known action, or None (which `run.perform` refuses)."""
    return _REGISTRY.get((name or "").strip().lower())


def available() -> list[tuple[str, str]]:
    """(name, label) for every action — the source for the prompt's action list."""
    return [(a.name, a.label) for a in _ACTIONS]


def _valid_app(name: str) -> bool:
    return bool(name) and "/" not in name and ".." not in name and _APP_NAME.match(name) is not None


def _valid_url(url: str) -> bool:
    return _HTTP_URL.match(url or "") is not None


def argv_for(action: Action, arg: str = "") -> list[str] | None:
    """Build the vetted argv for an 'argv' action, or None if the action is a diag / the arg fails
    its validator. Pure — the caller (`run.perform`) hands the result to safespawn unchanged."""
    if action.kind != "argv":
        return None
    if not action.takes_arg:
        return list(_STATIC_ARGV[action.name])
    arg = (arg or "").strip()
    if action.name == "open_app":
        return ["open", "-a", arg] if _valid_app(arg) else None
    if action.name == "open_url":
        return ["open", arg] if _valid_url(arg) else None
    if action.name == "web_search":
        return ["open", "https://www.google.com/search?q=" + quote(arg)] if arg else None
    return None


def render_action_prompt(actions: list[tuple[str, str]]) -> str:
    """The ACTIONS section appended to the system prompt (ADR-019). Lists the closed action set and
    teaches the `[[DO:]]` directive with worked examples — the same technique that fixed `[[REMEMBER]]`
    adherence. Pure: takes the action list so it renders whatever the runner actually offers."""
    lines = ["ACTIONS — you can DO things on the user's Mac. Available actions:"]
    lines += [f"- {name}: {label}" for name, label in actions]
    lines += [
        "To perform one, end your reply with a tag on its own line, written literally as:",
        "[[DO: <action>]]   — or for an action that takes an argument —   [[DO: <action>: <argument>]]",
        "Use ONLY an action from the list above; never invent one. You may both answer and act in the "
        "same message. Never mention or explain the tag — it is stripped before the user sees it.",
        "When you emit [[DO: report_system]], do NOT state or guess any system metric (CPU, memory, "
        "disk, battery) in your prose — the real report is appended automatically below your reply; "
        "defer to it entirely (e.g. say 'Let me check.' not 'Your CPU is at 0%').",
        "Worked examples:",
        "User: mute the volume",
        "Assistant: Muted.",
        "[[DO: mute]]",
        "User: what's my CPU doing?",
        "Assistant: Let me check.",
        "[[DO: report_system]]",
        "User: open google",
        "Assistant: Opening Google.",
        "[[DO: open_url: https://google.com]]",
    ]
    return "\n".join(lines)
