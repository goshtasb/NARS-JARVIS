"""ADR-057 — single-owner serialization for the one local model context.

The llama.cpp context is **non-reentrant**: only one inference may run at a time, and the daemon has
~9 callers of it (Tier-2 `converse`, the GBNF intent router, NL→Narsese translation, persona idle-batch,
the web-agent step, voice formatting, work-actions). Today they all run synchronously on the select()
thread, so a 512-token `converse` blocks the loop for seconds — the macOS beachball.

`LocalBrain` wraps the model so:

  • EVERY call is serialized by one lock (no two threads in the context at once → no crash).
  • the long `converse` generation runs OFF the loop, on a background thread. llama.cpp releases the GIL
    during the C decode, so the select() loop keeps iterating; completion is signalled on a self-pipe the
    loop already polls (same mechanism as the cloud/recall workers). The main loop never blocks on decode.

It is a transparent proxy: the inner model's methods are exposed unchanged (so `hasattr(brain, "…")`
still reflects the real model's capabilities — a no-GGUF demo source has no `generate_text`, and that
must keep surfacing), but the inference methods are wrapped to take the lock first.
"""
from __future__ import annotations

import os
import queue
import threading
import time

# The inference entry points on the model — these acquire the context lock. Everything else (cloud_complete,
# mode toggles, capability probes) is plain delegation: cloud I/O runs on its own thread and never touches
# the local context.
_SERIALIZED = frozenset({"generate", "generate_json", "generate_text", "to_claims", "create_chat_completion"})


class LocalBrain:
    def __init__(self, llm: object, idle_evict_s: float | None = None) -> None:
        self._llm = llm
        self._lock = threading.Lock()                 # the context is single-owner; serialize ALL access
        self._busy = False                            # a long async generation is in flight
        self._results: "queue.Queue[tuple]" = queue.Queue()
        self._r, self._w = os.pipe()                  # self-pipe: the worker wakes the select() loop
        os.set_blocking(self._r, False)
        # Phase 1 (memory): unload the heavy model after it has been idle this long, so its ~4.2 GB isn't
        # pinned while the user isn't using it (the 8/16 GB swap-freeze). Next call lazily reloads (~5 s).
        self._idle_evict_s = (float(os.environ.get("NARS_JARVIS_MODEL_IDLE_SEC", "300"))
                              if idle_evict_s is None else idle_evict_s)
        self._last_use = time.monotonic()
        self._evict_stop = threading.Event()
        self._evictor = threading.Thread(target=self._evict_loop, name="model-evictor", daemon=True)
        self._evictor.start()

    def __getattr__(self, name: str):
        # Only reached for names not found as real attributes (so the private fields/methods below are
        # never delegated). Delegating raises AttributeError if the inner model lacks `name`, which keeps
        # `hasattr(localbrain, "generate_text")` honest for a no-model demo source.
        if name.startswith("__") or name == "_llm":
            raise AttributeError(name)
        attr = getattr(self._llm, name)
        if name in _SERIALIZED and callable(attr):
            def locked(*a, **k):
                with self._lock:
                    try:
                        return attr(*a, **k)
                    finally:
                        self._last_use = time.monotonic()   # mark activity (reloads under this same lock)
            return locked
        return attr

    # ── the async Tier-2 path: long generation OFF the select() loop ──
    @property
    def busy(self) -> bool:
        """True while a submitted generation is decoding. The main-loop periodic callers (the web-agent
        step, the persona idle-batch) check this and skip a turn, so they never block on the lock."""
        return self._busy

    def fileno(self) -> int:
        return self._r

    def submit(self, token: int, system: str, user: str, max_tokens: int) -> None:
        """Decode one generation on a background thread. The result `(token, ok, text)` is queued and the
        self-pipe is poked so the select() loop drains it via `results()` on the next pass."""
        self._busy = True
        self._last_use = time.monotonic()
        threading.Thread(target=self._run, args=(token, system, user, max_tokens),
                         name="localbrain", daemon=True).start()

    def _run(self, token: int, system: str, user: str, max_tokens: int) -> None:
        try:
            with self._lock:                          # serialized against every other context user
                text = self._llm.generate_text(system, user, max_tokens=max_tokens)
            self._results.put((token, True, text))
        except Exception as exc:  # noqa: BLE001 — a model fault is reported to the caller, never crashes
            self._results.put((token, False, str(exc)))
        finally:
            self._busy = False
            self._last_use = time.monotonic()
            try:
                os.write(self._w, b"x")               # wake the loop (it polls self._r)
            except OSError:
                pass

    def results(self) -> list[tuple]:
        """Drain completed generations (called by the daemon when select() flags our fd readable)."""
        try:
            os.read(self._r, 65536)                   # clear the wake byte(s); non-blocking
        except (BlockingIOError, OSError):
            pass
        out: list[tuple] = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                break
        return out

    # ── Phase 1 (memory): idle eviction of the heavy model, on a background timer ──
    def _evict_loop(self) -> None:
        """Wake every 30 s and unload the model if it has been idle past the threshold. Exits promptly on
        close() (the Event short-circuits the wait)."""
        while not self._evict_stop.wait(30.0):
            try:
                self._maybe_evict()
            except Exception:  # noqa: BLE001 — a hiccup in maintenance must never take down the daemon
                pass

    def _maybe_evict(self) -> None:
        """Unload the model iff it is loaded, not busy, and idle past the threshold. Acquires the SAME lock
        every inference takes, so it can never evict mid-decode; the re-check under the lock closes the race
        where a call arrives between the idle test and the lock."""
        if self._busy:
            return
        if (time.monotonic() - self._last_use) < self._idle_evict_s:
            return
        if not getattr(self._llm, "loaded", False):           # nothing loaded (or DemoClaims) -> no-op
            return
        with self._lock:                                      # serialized against every context user
            if self._busy or (time.monotonic() - self._last_use) < self._idle_evict_s:
                return                                        # a decode raced in under the lock -> abort
            evict = getattr(self._llm, "evict", None)
            if callable(evict):
                evict()

    def close(self) -> None:
        self._evict_stop.set()                                # stop the evictor thread first
        if self._evictor.is_alive():
            self._evictor.join(timeout=1.0)
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass
