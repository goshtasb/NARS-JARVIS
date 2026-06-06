"""ONA subprocess wrapper: the bounded L1 reasoning cache (PRD §6).

Imperative Shell (S-02): all subprocess I/O is isolated here; parsing is delegated to the
pure functions in `parse`. The public interface is re-exported from `brain/__init__.py`.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .parse import Answer, parse_answer

# OpenNARS-for-Applications/NAR, relative to the repo root (two levels up from src/brain/).
_DEFAULT_NAR = Path(__file__).resolve().parents[2] / "OpenNARS-for-Applications" / "NAR"
_STEP_BARRIER = "done with 0 additional inference steps."


class Brain:
    """A handle to a running ONA reasoner process.

    Each instance owns one `./NAR shell` subprocess. Beliefs/goals are added and the reasoner
    is stepped; questions are answered from current memory with an evidence trail (stamp).
    """

    def __init__(self, nar_bin: str | None = None, cycles_per_step: int = 10,
                 motor_babbling: float = 0.0) -> None:
        path = nar_bin or os.environ.get("NARS_JARVIS_NAR_BIN") or str(_DEFAULT_NAR)
        if not Path(path).exists():
            raise FileNotFoundError(
                f"ONA NAR binary not found at {path}. "
                "Build it: (cd OpenNARS-for-Applications && sh build.sh)."
            )
        self._cycles = cycles_per_step
        self._proc = subprocess.Popen(
            [path, "shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._drain()  # clear any startup output
        # Override ONA's permissive game-agent default (0.2): NO random motor babbling on a live
        # host. Mirrors execution/autonomy.py MOTOR_BABBLING_CHANCE; kept as a literal default to
        # avoid a brain->execution cross-domain import (S-01). Verified accepted by the NAR shell.
        self._write(f"*motorbabbling={motor_babbling}")
        self._drain()

    def _write(self, line: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _drain(self) -> list[str]:
        """Send a 0-cycle marker and read until ONA's step barrier; return the lines before it."""
        assert self._proc.stdout is not None
        self._write("0")
        lines: list[str] = []
        while True:
            out = self._proc.stdout.readline()
            if out == "":  # EOF / process died
                break
            stripped = out.strip()
            if stripped == _STEP_BARRIER:
                break
            if not stripped or stripped.startswith("performing 0 "):
                continue
            lines.append(stripped)
        return lines

    def add_belief(self, narsese: str, cycles: int | None = None) -> list[str]:
        """Add a belief (e.g. '<a --> b>.') and run inference cycles. Returns ONA output lines."""
        self._write(narsese)
        self._write(str(self._cycles if cycles is None else cycles))
        return self._drain()

    def ask(self, narsese: str) -> Answer | None:
        """Ask a question (e.g. '<a --> c>?') and return the best Answer from memory, or None."""
        self._write(narsese)
        for line in self._drain():
            if line.startswith("Answer:"):
                return parse_answer(line)
        return None

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def __enter__(self) -> "Brain":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
