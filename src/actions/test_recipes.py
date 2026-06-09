"""Unit tests for the navigation recipe catalog (ADR-023): schema integrity, fail-safe lookup, and —
the central guarantee — that FRICTIONLESS vs GATED is read from DATA, never inferred. Pure."""
from actions import FRICTIONLESS, GATED, Recipe, nav_actions, recipe_for, should_gate
from actions.recipes import RECIPES


def test_every_recipe_is_well_formed() -> None:
    for r in RECIPES:
        assert r.verb in {"ax_set_value", "ax_press", "ax_set_checked"}, r
        assert r.friction in {FRICTIONLESS, GATED}, r
        assert r.intent and r.label and r.role and r.title, r
        # ax_set_value takes a user value (slider); ax_set_checked carries a fixed desired state
        # (idempotent toggle); ax_press takes neither.
        assert (r.verb == "ax_set_value") == r.takes_value, r
        if r.verb == "ax_set_checked":
            assert r.fixed_value in (0.0, 1.0) and not r.takes_value, r


def test_recipe_for_known_and_unknown() -> None:
    assert recipe_for("set_brightness") is not None
    assert recipe_for("  SET_BRIGHTNESS ") is not None     # case/space tolerant
    assert recipe_for("set_nukes") is None                 # unknown intent -> None (fail-safe)
    assert recipe_for("") is None


def test_should_gate_reads_friction_from_data() -> None:
    # The invariant: friction is the row's declared value, not anything the model chose.
    fr = Recipe("a", "a", None, "AXButton", "a", "ax_press", FRICTIONLESS, False)
    ga = Recipe("b", "b", None, "AXButton", "b", "ax_press", GATED, False)
    assert should_gate(fr) is False
    assert should_gate(ga) is True


def test_increase_contrast_is_idempotent_set_on() -> None:
    # v1.0 polish: "increase contrast" means ON (set-to-state), not a blind toggle.
    r = recipe_for("increase_contrast")
    assert r.verb == "ax_set_checked" and r.fixed_value == 1.0 and r.takes_value is False


def test_v1_recipes_are_frictionless() -> None:
    # v1 ships only the directly-labelled brightness slider (see RECIPES note on empty-title controls).
    assert should_gate(recipe_for("set_brightness")) is False


def test_nav_actions_lists_every_intent() -> None:
    names = {name for name, _ in nav_actions()}
    assert "set_brightness" in names
    assert all(label for _name, label in nav_actions())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("actions/test_recipes: OK")
