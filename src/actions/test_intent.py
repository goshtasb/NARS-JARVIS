"""ADR-054 Intent Router — pure grammar compiler + Interception Gate (no model, no I/O)."""
from actions.intent import build_intent_grammar, validate_intent


# ── grammar compiler ──
def test_grammar_enumerates_actions_plus_none_sentinel() -> None:
    g = build_intent_grammar(["summarize_file", "read_article"])
    assert r'"\"summarize_file\""' in g and r'"\"read_article\""' in g
    assert r'"\"none\""' in g                              # the escape hatch is always present
    assert "__ACTION_ENUM__" not in g                      # placeholder fully substituted


def test_grammar_arg_string_permits_paths_and_urls() -> None:
    g = build_intent_grammar(["summarize_file"])
    # the JSON-body class [^"\\] admits / . : ~ etc. by construction (not an explicit allowlist)
    assert r'strchar ::= [^"\\]' in g
    assert r'\"timing\"' in g                              # timing field present (escaped in GBNF)
    assert "at_clock_hour" in g and "in_minutes" in g      # the timing kinds are constrained


def test_grammar_handles_empty_action_list() -> None:
    g = build_intent_grammar([])
    assert r'"\"none\""' in g                              # still has the sentinel -> always valid


def test_pinned_grammar_forces_verb_without_none() -> None:
    # the / override: a single pinned verb, no escape hatch -> the model cannot decline the user's choice
    g = build_intent_grammar(["summarize_file"], include_none=False)
    assert r'"\"summarize_file\""' in g and r'"\"none\""' not in g


# ── Interception Gate ──
VALID = {"summarize_file", "read_article", "report_system"}
ARG_REQ = {"summarize_file", "read_article"}


def test_gate_accepts_well_formed_now_job() -> None:
    ok, out = validate_intent({"action": "summarize_file", "arg": "/Users/me/PRD.pdf", "timing": None},
                              VALID, ARG_REQ)
    assert ok and out == {"action": "summarize_file", "arg": "/Users/me/PRD.pdf", "timing": None}


def test_gate_passes_nonexistent_path_through_unchecked() -> None:
    # the design call: the gate does NOT touch the filesystem; a typo'd path is a Canvas/runtime concern
    ok, out = validate_intent({"action": "summarize_file", "arg": "/Users/me/typo-does-not-exist.pdf",
                               "timing": None}, VALID, ARG_REQ)
    assert ok and out["arg"] == "/Users/me/typo-does-not-exist.pdf"


def test_gate_rejects_none_sentinel() -> None:
    ok, out = validate_intent({"action": "none", "arg": "", "timing": None}, VALID, ARG_REQ)
    assert not ok and "rephrase" in out["clarify"].lower()


def test_gate_rejects_missing_required_arg() -> None:
    ok, out = validate_intent({"action": "summarize_file", "arg": "  ", "timing": None}, VALID, ARG_REQ)
    assert not ok and "needs a target" in out["clarify"]


def test_gate_allows_argless_action_with_empty_arg() -> None:
    ok, out = validate_intent({"action": "report_system", "arg": "", "timing": None}, VALID, ARG_REQ)
    assert ok and out["action"] == "report_system"


def test_gate_normalizes_and_bounds_timing() -> None:
    ok, out = validate_intent({"action": "report_system", "arg": "",
                               "timing": {"kind": "in_minutes", "value": 120}}, VALID, ARG_REQ)
    assert ok and out["timing"] == {"kind": "in_minutes", "value": 120}
    # "now" normalizes to None (manual / Run Now)
    ok, out = validate_intent({"action": "report_system", "arg": "",
                               "timing": {"kind": "now", "value": 0}}, VALID, ARG_REQ)
    assert ok and out["timing"] is None
    # clock hour in range
    ok, out = validate_intent({"action": "report_system", "arg": "",
                               "timing": {"kind": "at_clock_hour", "value": 23}}, VALID, ARG_REQ)
    assert ok and out["timing"]["value"] == 23


def test_gate_rejects_out_of_range_or_malformed_timing() -> None:
    for bad in ({"kind": "at_clock_hour", "value": 99}, {"kind": "in_minutes", "value": 0},
                {"kind": "in_minutes", "value": 99999}, {"kind": "bogus", "value": 1}, "tonight"):
        ok, out = validate_intent({"action": "report_system", "arg": "", "timing": bad}, VALID, ARG_REQ)
        assert not ok and "when to run" in out["clarify"]


def test_gate_rejects_unknown_action_defense_in_depth() -> None:
    ok, out = validate_intent({"action": "rm_rf_slash", "arg": "/", "timing": None}, VALID, ARG_REQ)
    assert not ok and "don't have an action" in out["clarify"]
