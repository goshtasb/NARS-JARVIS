"""Concrete OmniGlass SandboxClient — the live seam (M3 Phase B, air-gapped). Imperative Shell.

Runs a FIXED argv tuple under macOS `sandbox-exec` with the audited air-gapped profile and a
FILTERED environment (mirrors OmniGlass `env_filter.rs` — the sandbox profile itself does NOT
strip secrets from the environment, per the 2026-06-05 crucible). It accepts ONLY a bounded
`tuple[str, ...]`; there is no shell string and no interpolation anywhere in this file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PROFILE = Path(__file__).resolve().parent / "profiles" / "air_gapped.sb"

# Non-secret runtime vars the child may keep (mirrors env_filter.rs ESSENTIAL_VARS, minus secrets).
_ESSENTIAL_ENV = ("PATH", "HOME", "USER", "LANG", "TERM", "SHELL")
# A forwarded variable NAME containing any of these is never passed through (defense-in-depth).
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "AWS", "ANTHROPIC", "OPENAI")


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


class AirGappedSandboxClient:
    """`sandbox-exec`-backed client for air-gapped saved commands. No network, no shell, no secrets."""

    def __init__(self, profile_path: Path | None = None, timeout: float = 15.0) -> None:
        self._profile = Path(profile_path) if profile_path else _PROFILE
        if not self._profile.exists():
            raise FileNotFoundError(f"sandbox profile not found: {self._profile}")
        self._timeout = timeout

    def _filtered_env(self) -> dict[str, str]:
        return {k: os.environ[k] for k in _ESSENTIAL_ENV
                if k in os.environ and not _looks_secret(k)}

    def env_filter_verified(self) -> bool:
        """True only if the env we will hand the child carries no secret-bearing variable name."""
        return not any(_looks_secret(k) for k in self._filtered_env())

    def run_sandboxed(self, argv: tuple[str, ...]) -> bool:
        """Run the fixed argv under sandbox-exec with the filtered env. Returns success (rc == 0)."""
        if not isinstance(argv, tuple) or not argv or not all(isinstance(a, str) for a in argv):
            raise TypeError("argv must be a non-empty tuple[str, ...] — no shell strings")
        cmd = ["sandbox-exec", "-f", str(self._profile), *argv]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  env=self._filtered_env(), cwd="/", timeout=self._timeout)
        except subprocess.TimeoutExpired:
            return False
        return proc.returncode == 0
