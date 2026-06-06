"""Immutable command templates for each catalog operation. Functional Core (S-02) — DATA ONLY.

These are the vetted, human-reviewed, version-controlled commands an OmniGlass executor would
submit. They are **argv tuples, not shell strings** — reinforcing "no generative string
execution": even the command form is fixed and un-interpolated. Nothing here is executed.
"""
from __future__ import annotations

from .catalog import AppEnum, OpName, Operation, SavedCommandEnum

_APP_TEMPLATES: dict[AppEnum, tuple[str, ...]] = {
    AppEnum.SLACK: ("open", "-a", "Slack"),
    AppEnum.SLIDES: ("open", "-a", "Keynote"),
    AppEnum.TERMINAL: ("open", "-a", "Terminal"),
}

_SAVED_COMMAND_TEMPLATES: dict[SavedCommandEnum, tuple[str, ...]] = {
    SavedCommandEnum.DISK_USAGE: ("df", "-h"),
    SavedCommandEnum.LIST_PROCESSES: ("ps", "-ax"),
}


def command_for(operation: Operation) -> tuple[str, ...]:
    """Return the fixed argv template for a validated Operation. Pure; raises KeyError if unmapped."""
    if operation.name == OpName.OPEN_APP:
        return _APP_TEMPLATES[operation.arg]  # type: ignore[index]
    if operation.name == OpName.RUN_SAVED_COMMAND:
        return _SAVED_COMMAND_TEMPLATES[operation.arg]  # type: ignore[index]
    raise KeyError(f"no command template for {operation.name}")
