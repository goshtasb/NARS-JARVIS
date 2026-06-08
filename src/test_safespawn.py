"""Unit tests for the sanctioned subprocess seam (ADR-015)."""
import os

import pytest

import safespawn


def test_scrub_environ_removes_secrets_keeps_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-xxx")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "yyy")
    monkeypatch.setenv("MY_TOKEN", "zzz")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/Users/x")
    monkeypatch.setenv("NARS_JARVIS_LLM_GGUF", "/models/m.gguf")
    removed = safespawn.scrub_environ()
    assert "ANTHROPIC_API_KEY" in removed and "AWS_SECRET_ACCESS_KEY" in removed and "MY_TOKEN" in removed
    assert "ANTHROPIC_API_KEY" not in os.environ and "MY_TOKEN" not in os.environ
    assert os.environ.get("PATH") == "/usr/bin"                 # runtime vars retained
    assert os.environ.get("HOME") == "/Users/x"
    assert os.environ.get("NARS_JARVIS_LLM_GGUF") == "/models/m.gguf"   # non-secret config retained
    assert safespawn.scrub_environ() == []                      # idempotent


def test_run_rejects_shell_string() -> None:
    with pytest.raises(TypeError):
        safespawn.run("echo hi")                                # str argv -> shell-string ban
    with pytest.raises(ValueError):
        safespawn.run(["echo", "hi"], shell=True)               # shell=True forbidden


def test_run_refuses_secret_env() -> None:
    with pytest.raises(ValueError):
        safespawn.run(["/bin/echo", "hi"], env={"ANTHROPIC_API_KEY": "sk"})


def test_run_clean_spawn_works() -> None:
    out = safespawn.run(["/bin/echo", "ok"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok"


def test_looks_secret() -> None:
    assert safespawn.looks_secret("OPENAI_API_KEY") and safespawn.looks_secret("db_password")
    assert not safespawn.looks_secret("PATH") and not safespawn.looks_secret("NARS_JARVIS_DB")


if __name__ == "__main__":
    test_run_rejects_shell_string()
    test_run_refuses_secret_env()
    test_run_clean_spawn_works()
    test_looks_secret()
    print("test_safespawn: OK (run scrub test via pytest for monkeypatch)")
