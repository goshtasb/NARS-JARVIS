"""ONA subprocess wrapper: the bounded L1 reasoning cache (PRD §6).

Imperative Shell (S-02): all subprocess I/O is isolated here; parsing is delegated to the
pure functions in `parse`. The public interface is re-exported from `brain/__init__.py`.

Resilience (ADR-036): malformed Narsese can kill the NAR subprocess (BrokenPipeError). When an
`on_restart` hook is provided, the Brain relaunches a fresh NAR and lets the owner replay beliefs
(from its durable store), retrying the operation once — bounded by `max_restarts`, after which it
raises `BrainUnavailable` so the caller can fail closed. With no hook (the default) behavior is
unchanged: a dead pipe propagates, exactly as before.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

import safespawn

from .parse import Answer, parse_answer, parse_line

# OpenNARS-for-Applications/NAR, relative to the repo root (two levels up from src/brain/).
_DEFAULT_NAR = Path(__file__).resolve().parents[2] / "OpenNARS-for-Applications" / "NAR"
_STEP_BARRIER = "done with 0 additional inference steps."


class BrainUnavailable(RuntimeError):
    """The NAR subprocess died and could not be recovered within `max_restarts`. Callers that opted
    into resilience should catch this and fail closed (ADR-036)."""


class Brain:
    """A handle to a running ONA reasoner process.

    Each instance owns one `./NAR shell` subprocess. Beliefs/goals are added and the reasoner
    is stepped; questions are answered from current memory with an evidence trail (stamp).
    """

    def __init__(self, nar_bin: str | None = None, cycles_per_step: int = 10,
                 motor_babbling: float = 0.0,
                 on_restart: Callable[["Brain"], None] | None = None, max_restarts: int = 3) -> None:
        path = nar_bin or os.environ.get("NARS_JARVIS_NAR_BIN") or str(_DEFAULT_NAR)
        if not Path(path).exists():
            raise FileNotFoundError(
                f"ONA NAR binary not found at {path}. "
                "Build it: (cd OpenNARS-for-Applications && sh build.sh)."
            )
        self._path = path
        self._cycles = cycles_per_step
        self._babbling = motor_babbling
        self._on_restart = on_restart          # ADR-036: owner re-feeds beliefs after a relaunch
        self._max_restarts = max_restarts
        self._in_restart = False               # re-entrancy guard during replay
        self._evidence: dict[int, str] = {}    # stamp id -> term (session-scoped evidence trace)
        self._spawn()

    def _spawn(self) -> None:
        """Launch (or relaunch) the NAR shell and apply session config. Used by __init__ + restart."""
        self._proc = safespawn.popen(
            [self._path, "shell"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._drain()  # clear any startup output
        # Override ONA's permissive game-agent default (0.2): NO random motor babbling on a live host.
        self._write(f"*motorbabbling={self._babbling}")
        self._drain()

    # ── resilience (ADR-036) ──
    def _relaunch(self) -> None:
        """Kill the dead NAR and start a fresh one. Evidence ids are session-scoped, so reset them
        (replay repopulates). Does NOT replay beliefs — that's the owner's `on_restart` hook."""
        try:
            self.close()
        except Exception:  # noqa: BLE001 — the old proc is already broken; ignore teardown errors
            pass
        self._evidence.clear()
        self._spawn()

    def _guarded(self, fn: Callable[[], object]) -> object:
        """Run a write+read op; on a dead/broken NAR, relaunch + replay + retry — up to `max_restarts`
        attempts within this call, then raise `BrainUnavailable`. With no `on_restart` hook, behaviour is
        unchanged (errors propagate). Re-entrant calls (during replay) run raw, never nesting a restart."""
        if self._on_restart is None or self._in_restart:
            return fn()
        attempts = 0
        while True:
            try:
                if self._proc.poll() is not None:
                    raise BrokenPipeError("NAR process is not running")
                return fn()
            except (BrokenPipeError, OSError, ValueError):
                if attempts >= self._max_restarts:
                    raise BrainUnavailable(f"NAR died and exceeded {self._max_restarts} restarts")
                attempts += 1
                self._relaunch()
                self._in_restart = True
                try:
                    self._on_restart(self)                  # owner replays beliefs from its store
                finally:
                    self._in_restart = False
                # loop: retry the op on the fresh process

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
        """Add a belief (e.g. '<a --> b>.') and run inference cycles. Returns ONA output lines.

        Records each accepted input's evidence-stamp id -> term (from the 'Input:' echo) so a later
        answer's stamp can be unrolled back to the real premises. Session-scoped: ONA reassigns
        stamp ids on reload, and this is repopulated wherever beliefs (re)enter L1.
        """
        return self._guarded(lambda: self._add_belief_raw(narsese, cycles))  # type: ignore[return-value]

    def _add_belief_raw(self, narsese: str, cycles: int | None) -> list[str]:
        self._write(narsese)
        self._write(str(self._cycles if cycles is None else cycles))
        out = self._drain()
        for line in out:
            if line.startswith("Input:"):
                ev = parse_line(line)
                if ev is not None and ev.term:
                    for stamp_id in ev.stamp:
                        self._evidence[stamp_id] = ev.term
        return out

    def evidence_terms(self, stamp: tuple[int, ...]) -> list[str]:
        """Map an answer's evidence stamp back to the premise TERMS that produced it (real ones
        only; an unmapped id — evicted or internal — is skipped, never fabricated)."""
        return [self._evidence[s] for s in stamp if s in self._evidence]

    def ask(self, narsese: str) -> Answer | None:
        """Ask a question (e.g. '<a --> c>?') and return the best Answer from memory, or None."""
        return self._guarded(lambda: self._ask_raw(narsese))  # type: ignore[return-value]

    def _ask_raw(self, narsese: str) -> Answer | None:
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
