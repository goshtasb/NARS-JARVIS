"""Slice 3a — the deviation-scan worker subprocess contract (fail-soft).

The daemon multiplexes this worker's stdout, so it must ALWAYS speak the tagged line protocol and exit 0,
never crash with a traceback. In CI there is no GGUF model, so the worker reports a clean `[error] model
unavailable` (or `[error] worker import failed` if llama_cpp is absent); with a model wired it would emit
`[pending]` + `[result]`. This asserts the contract holds in either case — no traceback, clean exit.
"""
import os
import subprocess
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE = os.path.join(_SRC, "triage", "fixtures", "nda_born_digital.pdf")


def test_worker_speaks_protocol_and_exits_clean() -> None:
    env = {k: v for k, v in os.environ.items() if k != "NARS_JARVIS_LLM_GGUF"}   # force model-unavailable path
    proc = subprocess.run([sys.executable, "-m", "service.triage_worker", _FIXTURE, ":memory:"],
                          cwd=_SRC, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    assert "Traceback" not in proc.stdout and "Traceback" not in proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines, "worker emitted nothing on stdout"
    # every emitted line is a protocol-tagged frame, and a terminal one is present
    assert all(ln.startswith("[") for ln in lines)
    assert any(ln.startswith(("[result] ", "[error] ")) for ln in lines)


def test_worker_reports_missing_file_cleanly() -> None:
    proc = subprocess.run([sys.executable, "-m", "service.triage_worker", "/no/such/file.pdf", ":memory:"],
                          cwd=_SRC, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and "Traceback" not in proc.stderr
    assert proc.stdout.startswith("[error] ") and "no such file" in proc.stdout
