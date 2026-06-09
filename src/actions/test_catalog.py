"""Unit tests for the closed action catalog (ADR-019): resolution, the prompt list, and the
allowlist arg sanitizers (the security boundary). Pure — no spawn, no OS."""
from actions import catalog


def test_resolve_known_and_unknown() -> None:
    assert catalog.resolve("mute") is not None
    assert catalog.resolve("MUTE ") is not None            # case/whitespace tolerant
    assert catalog.resolve("rm_rf") is None                # not in the catalog
    assert catalog.resolve("") is None


def test_available_lists_every_action() -> None:
    names = {name for name, _label in catalog.available()}
    assert {"report_system", "dark_mode", "mute", "open_app", "open_url", "web_search"} <= names
    assert all(label for _name, label in catalog.available())  # every action is described


def test_ax_verbs_are_closed_confirm_and_hidden_from_prompt_list() -> None:
    # ADR-021: ax verbs are a closed set, kind="ax", consent-gated, and EXCLUDED from the static
    # prompt list (surfaced contextually alongside the live AX DOM instead).
    assert catalog.AX_VERBS == frozenset({"ax_press", "ax_set_value"})
    for v in catalog.AX_VERBS:
        a = catalog.resolve(v)
        assert a is not None and a.kind == "ax" and a.confirm is True
    listed = {name for name, _ in catalog.available()}
    assert not (catalog.AX_VERBS & listed)               # not in the static action list
    assert catalog.argv_for(catalog.resolve("ax_press"), "btn_1") is None  # never argv/safespawn


def test_static_action_builds_fixed_argv() -> None:
    argv = catalog.argv_for(catalog.resolve("mute"))
    assert argv == ["osascript", "-e", "set volume output muted true"]


def test_diag_action_has_no_argv() -> None:
    assert catalog.argv_for(catalog.resolve("report_system")) is None  # diag, not argv


def test_open_app_accepts_real_app_names() -> None:
    for name in ("Safari", "Google Chrome", "Visual Studio Code", "Notes"):
        assert catalog.argv_for(catalog.resolve("open_app"), name) == ["open", "-a", name]


def test_open_app_rejects_paths_flags_and_metachars() -> None:
    # The exact attack surface from the design review: path execution, flag injection, shell metachars.
    for bad in ("/bin/bash", "../evil", "--args", "-W", "a;b", "App && rm", "a/b", ""):
        assert catalog.argv_for(catalog.resolve("open_app"), bad) is None, bad


def test_open_url_requires_http_scheme() -> None:
    assert catalog.argv_for(catalog.resolve("open_url"), "https://google.com") == ["open", "https://google.com"]
    assert catalog.argv_for(catalog.resolve("open_url"), "http://x.test/p") == ["open", "http://x.test/p"]
    for bad in ("file:///etc/passwd", "/etc/passwd", "ftp://x", "google.com", ""):
        assert catalog.argv_for(catalog.resolve("open_url"), bad) is None, bad


def test_web_search_url_encodes_the_query() -> None:
    argv = catalog.argv_for(catalog.resolve("web_search"), "best tacos & burritos")
    assert argv[0:1] == ["open"]
    assert argv[1].startswith("https://www.google.com/search?q=")
    assert " " not in argv[1] and "&" not in argv[1].split("q=", 1)[1]  # encoded, no injection
    assert catalog.argv_for(catalog.resolve("web_search"), "") is None


def test_render_action_prompt_lists_actions_and_teaches_the_tag() -> None:
    text = catalog.render_action_prompt(catalog.available())
    assert "[[DO:" in text
    assert "report_system" in text and "mute" in text       # actions enumerated
    assert "open_url: https://google.com" in text           # a worked argument example


def test_render_action_prompt_forbids_guessing_metrics() -> None:
    # Trust fix: the model must not invent CPU/mem numbers in prose when it calls report_system —
    # defer to the appended deterministic report (the live "your CPU is 0%" vs real 11% discrepancy).
    text = catalog.render_action_prompt(catalog.available())
    assert "do NOT state or guess any system metric" in text


def test_render_action_prompt_forbids_improvising_unavailable_actions() -> None:
    # Honesty fix: lacking a contrast action, the 7B improvised open_app cascades + claimed success.
    # The prompt must forbid improvising and faking unavailable capabilities.
    text = catalog.render_action_prompt(catalog.available())
    assert "Do NOT improvise" in text and "NEVER claim to have changed a setting" in text
