"""The single sanctioned subprocess seam (ADR-015). Imperative Shell (S-02).

Security hardening of the execution surface. Two structural guarantees:

1. `scrub_environ()` — called ONCE at daemon/console boot, BEFORE anything spawns — pops every
   secret-bearing variable out of `os.environ` in place. Because Python's `subprocess` inherits
   `os.environ` by default, removing secrets from the process environment makes that default
   **safe by construction**: a future raw spawn has no keys left to leak. The root fix, not a
   per-call-site convention.
2. `run()`/`popen()` — the only sanctioned spawn wrappers: they reject shell strings (`shell=True`
   and non-list argv) and refuse to pass any secret-bearing env var. `test_no_raw_subprocess`
   enforces that NO other module calls `subprocess` directly, so this seam can't be bypassed.

The app needs no cloud key at runtime (local GGUF / whisper / `say` / `df` / ONA), so the secrets
in the environment are purely ambient (inherited from the launching shell) — safe to strip.
"""
from __future__ import annotations

import os
import subprocess

# A variable whose NAME contains any of these is treated as secret and never inherited by a child.
SECRET_MARKERS: tuple[str, ...] = ("KEY", "SECRET", "TOKEN", "PASSWORD", "AWS", "ANTHROPIC", "OPENAI")


def looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_MARKERS)


def scrub_environ() -> list[str]:
    """Remove secret-bearing variables from this process's `os.environ`, in place. Idempotent.
    Call at the very top of any process entrypoint that may spawn children. Returns the names
    removed (for a one-line boot log; never the values)."""
    removed = [name for name in list(os.environ) if looks_secret(name)]
    for name in removed:
        os.environ.pop(name, None)
    return removed


def _check(argv, kwargs: dict) -> None:
    if kwargs.get("shell"):
        raise ValueError("safespawn: shell=True is forbidden (no shell execution path)")
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("safespawn: argv must be a non-empty list/tuple of str — never a shell string")
    env = kwargs.get("env")
    if env is not None and any(looks_secret(k) for k in env):
        raise ValueError("safespawn: refusing to pass a secret-bearing variable to a child process")


def run(argv, **kwargs):
    """`subprocess.run` with the shell-string ban + secret-env refusal. argv MUST be a list/tuple."""
    _check(argv, kwargs)
    return subprocess.run(argv, **kwargs)


def popen(argv, **kwargs):
    """`subprocess.Popen` with the shell-string ban + secret-env refusal. argv MUST be a list/tuple."""
    _check(argv, kwargs)
    return subprocess.Popen(argv, **kwargs)
