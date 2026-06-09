"""The navigation recipe catalog (ADR-023). Functional Core (S-02) — pure, frozen, the single source
of truth for the FRICTIONLESS ⟷ GATED boundary.

The LLM proposes an *intent* (e.g. `set_brightness 45`); this declarative table maps that intent to an
**immutable** policy: which surface to open, which control to actuate, with which verb, and — crucially
— whether it is FRICTIONLESS (curated/safe/reversible → no consent) or GATED (→ human approval). The
friction decision is *data here*, never a probabilistic LLM choice (see ADR-022's brightness bug).

Adding an OS domain (Sound, Wi-Fi, Night Shift, an app action) is a new `Recipe` row — no branch logic.
Fail-safe: an intent absent from this table is unknown to `recipe_for` and the general (always-GATED)
`ax_*` verbs handle it instead; an unmatched control simply never actuates.

NOTE: each row's `(surface, role, title)` must match what macOS actually exposes in the accessibility
tree — verify against a live serializer dump (the daemon `status` command), never guess.
"""
from __future__ import annotations

from dataclasses import dataclass

FRICTIONLESS = "frictionless"   # curated, safe, reversible — actuates with no consent
GATED = "gated"                 # requires the ADR-020 human-approval gate

# Deep links to the System Settings surfaces a recipe opens (the eyes can't see what isn't on screen).
_DISPLAYS = "x-apple.systempreferences:com.apple.Displays-Settings.extension"


@dataclass(frozen=True)
class Recipe:
    """One intent → (open this surface, find this control, actuate with this verb) + friction policy."""
    intent: str           # the [[DO:]] verb name, e.g. "set_brightness"
    label: str            # human description (prompt list + acks)
    surface: str | None   # deep link to open first, or None if the control is already reachable
    role: str             # AX role of the target, e.g. "AXSlider" / "AXCheckBox"
    title: str            # AX title/desc substring (case-insensitive) identifying the control
    verb: str             # "ax_set_value" (sliders) | "ax_press" (toggles/buttons)
    friction: str         # FRICTIONLESS | GATED
    takes_value: bool     # True if the intent carries a value (sliders); False for toggles


# The closed table. v1 ships the one recipe whose target macOS labels directly in the accessibility
# tree — the Brightness slider. A live AX dump (ADR-023 verification) showed that MANY System Settings
# controls (e.g. the Accessibility → Display checkboxes, including "Increase contrast") expose an EMPTY
# AXTitle — their label lives in a *sibling* element, so title-matching can't reach them. Reaching
# those toggles requires enriching the serializer to associate sibling labels with controls; that is
# deferred to ADR-024. Adding any new recipe = a row here AFTER confirming its (role, title) against a
# live dump via the daemon `axdump` command — never guessed. The schema already supports `ax_press`
# toggles and GATED friction; only the live label availability is the gating factor.
RECIPES: tuple[Recipe, ...] = (
    Recipe("set_brightness", "set the display brightness (0-100)",
           _DISPLAYS, "AXSlider", "brightness", "ax_set_value", FRICTIONLESS, takes_value=True),
)

_BY_INTENT: dict[str, Recipe] = {r.intent: r for r in RECIPES}


def recipe_for(intent: str) -> Recipe | None:
    """The recipe for an intent, or None (fail-safe: unknown intents are not frictionless)."""
    return _BY_INTENT.get((intent or "").strip().lower())


def nav_actions() -> list[tuple[str, str]]:
    """(intent, label) for every recipe — the source for the catalog's nav actions + prompt list."""
    return [(r.intent, r.label) for r in RECIPES]


def should_gate(recipe: Recipe) -> bool:
    """The ONE friction decision, read straight from the row's data — never inferred or model-chosen."""
    return recipe.friction == GATED
