"""Unit tests for action execution (ADR-019). A recording fake replaces the spawn, so NO real OS
side effect occurs — we assert the argv that *would* run, and that rejected args never spawn at all."""
from actions import ActionRunner
from actions.run import ConsentSpec, perform


class _FakeSpawn:
    """Records argv instead of running it — proves what would execute with zero side effects."""
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return None


def test_static_action_builds_and_runs_vetted_argv() -> None:
    fake = _FakeSpawn()
    out = perform("mute", spawn=fake)
    assert fake.calls == [["osascript", "-e", "set volume output muted true"]]
    assert "Done" in out


def test_open_url_runs_safe_argv() -> None:
    fake = _FakeSpawn()
    perform("open_url", "https://example.com", spawn=fake)
    assert fake.calls == [["open", "https://example.com"]]


def test_open_app_runs_with_dash_a() -> None:
    fake = _FakeSpawn()
    perform("open_app", "Google Chrome", spawn=fake)
    assert fake.calls == [["open", "-a", "Google Chrome"]]


def test_web_search_encodes_and_runs() -> None:
    fake = _FakeSpawn()
    perform("web_search", "tacos near me", spawn=fake)
    assert len(fake.calls) == 1 and fake.calls[0][0] == "open"
    assert fake.calls[0][1].startswith("https://www.google.com/search?q=")


def test_unknown_action_never_spawns() -> None:
    fake = _FakeSpawn()
    out = perform("delete_everything", spawn=fake)
    assert fake.calls == []                     # refused before any spawn
    assert "don't know" in out.lower()


def test_unsafe_arg_never_spawns() -> None:
    # The security guarantee: a rejected argument produces a refusal string, NOT a spawn.
    fake = _FakeSpawn()
    for name, bad in [("open_app", "/bin/bash"), ("open_app", "--args"),
                      ("open_url", "file:///etc/passwd"), ("open_url", "/etc/passwd")]:
        out = perform(name, bad, spawn=fake)
        assert "can't do that" in out.lower(), (name, bad, out)
    assert fake.calls == []                     # nothing spawned across all rejects


def test_report_system_returns_a_report_without_spawning() -> None:
    fake = _FakeSpawn()
    out = perform("report_system", spawn=fake)
    assert "System report:" in out
    assert fake.calls == []                     # diagnostics is psutil-only, no spawn


def test_action_runner_exposes_available_and_perform() -> None:
    fake = _FakeSpawn()
    runner = ActionRunner(spawn=fake)
    assert any(name == "mute" for name, _label in runner.available())
    runner.perform("mute")
    assert fake.calls == [["osascript", "-e", "set volume output muted true"]]


def test_propose_reversible_runs_immediately() -> None:
    fake = _FakeSpawn()
    result, spec = ActionRunner(spawn=fake).propose("mute")
    assert spec is None and "Done" in result          # reversible -> ran now, no consent
    assert fake.calls == [["osascript", "-e", "set volume output muted true"]]


def test_propose_destructive_defers_to_consent() -> None:
    # empty_trash is confirm=True -> propose returns a ConsentSpec and does NOT spawn until approved.
    fake = _FakeSpawn()
    runner = ActionRunner(spawn=fake)
    result, spec = runner.propose("empty_trash")
    assert result is None and isinstance(spec, ConsentSpec)
    assert fake.calls == []                            # nothing ran yet
    out = spec.on_approve()                            # the consent gate would call this on approval
    assert fake.calls == [["osascript", "-e", 'tell application "Finder" to empty trash']]
    assert "Done" in out


def test_propose_unknown_action_no_spec_no_spawn() -> None:
    fake = _FakeSpawn()
    result, spec = ActionRunner(spawn=fake).propose("nuke")
    assert spec is None and "don't know" in result.lower() and fake.calls == []
