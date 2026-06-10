"""Unit tests for action execution (ADR-019). A recording fake replaces the spawn, so NO real OS
side effect occurs — we assert the argv that *would* run, and that rejected args never spawn at all."""
from actions import ActionRunner
from actions.run import ConsentSpec, perform


class _FakeSpawn:
    """Records argv instead of running it — proves what would execute with zero side effects. By
    default simulates success (returncode 0); pass returncode/stderr to simulate a failed command."""
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._rc = returncode
        self._stderr = stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return type("R", (), {"returncode": self._rc, "stderr": self._stderr})()


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


def test_nonzero_exit_reports_failure_not_done() -> None:
    # ADR-019 follow-up: a child that exits non-zero (e.g. `open -a Accessibility` -> exit 1) must be
    # reported truthfully, not as "(Done:)". The spawn does not raise on non-zero — perform checks rc.
    fake = _FakeSpawn(returncode=1, stderr="Unable to find application named 'Accessibility'")
    out = perform("open_app", "Accessibility", spawn=fake)
    assert fake.calls == [["open", "-a", "Accessibility"]]      # it did attempt it
    assert "Done" not in out                                    # but never claims success
    assert "Couldn't" in out and "Unable to find application named 'Accessibility'" in out


def test_nonzero_exit_without_stderr_reports_exit_code() -> None:
    fake = _FakeSpawn(returncode=2, stderr="")
    out = perform("mute", spawn=fake)
    assert "Done" not in out and "exit code 2" in out


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


# ── ADR-032: kind="work" routes to documents, with the injected llm; never spawns ──
def test_work_summarize_routes_to_llm_and_never_spawns() -> None:
    import os, tempfile
    p = tempfile.mktemp(suffix=".txt")
    open(p, "w").write("alpha beta gamma " * 40)

    class _FakeLLM:
        def __init__(self): self.calls = 0
        def generate_text(self, system, user, max_tokens=64):
            self.calls += 1
            return "a summary"
    fake_spawn, llm = _FakeSpawn(), _FakeLLM()
    out = ActionRunner(spawn=fake_spawn, llm=llm).perform("summarize_file", p)
    assert "Summarized" in out and llm.calls >= 1 and fake_spawn.calls == []   # used the model, not safespawn


def test_work_summarize_without_model_is_honest() -> None:
    import tempfile
    p = tempfile.mktemp(suffix=".txt"); open(p, "w").write("hi")
    out = ActionRunner(spawn=_FakeSpawn(), llm=None).perform("summarize_file", p)
    assert out.startswith("⚠ No local model")


def test_work_read_file_needs_no_model() -> None:
    import os, tempfile
    p = tempfile.mktemp(suffix=".md"); open(p, "w").write("# title\nbody")
    out = ActionRunner(spawn=_FakeSpawn(), llm=None).perform("read_file", p)
    assert "Read" in out and "→" in out
