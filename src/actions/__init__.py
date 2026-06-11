"""actions — conversational Mac actions + system diagnostics (ADR-019).

The LLM proposes an action with a `[[DO: <action>]]` directive (parsed in `language.extract`); the
CLOSED `catalog` validates it and the `run` shell executes it through the sanctioned `safespawn`
seam (ADR-015). `diagnostics` answers "what's my CPU / is anything wrong" read-only via psutil. No
generative execution path exists — only the enumerated actions can run.

Public interface (ADR-001: a module's surface is its `__init__.py` + `__all__`).
"""
from .catalog import AX_VERBS, Action, argv_for, available, render_action_prompt, resolve, schema
from .diagnostics import (
    anomaly_flags,
    drop_nominal_verdict,
    largest_apps_report,
    net_report,
    system_report,
)
from .recipes import FRICTIONLESS, GATED, Recipe, nav_actions, recipe_for, should_gate
from .run import ActionRunner, ConsentSpec, perform

__all__ = [
    "Action",
    "AX_VERBS",
    "resolve",
    "available",
    "schema",
    "argv_for",
    "render_action_prompt",
    "system_report",
    "net_report",
    "largest_apps_report",
    "drop_nominal_verdict",
    "anomaly_flags",
    "ActionRunner",
    "ConsentSpec",
    "perform",
    "Recipe",
    "recipe_for",
    "nav_actions",
    "should_gate",
    "FRICTIONLESS",
    "GATED",
]
