"""System sentinel — Imperative Shell (S-02). Drives the pure cores from psutil + watchdog.

Observe-only (C3): emits discretized Narsese events to a sink (e.g. brain.add_belief). ALL
flooding-prevention math lives in the pure cores (schmitt/rollup/limiter); this layer is the
thin binding. psutil/watchdog are imported lazily so the pure cores test without them.
"""
from __future__ import annotations

import time
from typing import Callable

from .limiter import CAPACITY, RATE, BucketState, try_consume
from .narsese import activity_event, signal_event
from .rollup import RollupState, on_event, on_tick
from .schmitt import CPU_LADDER, MEM_LADDER, DiscState, step

Sink = Callable[[str], None]


class SystemSentinel:
    def __init__(self, sink: Sink, watch_dirs: list[str] | None = None,
                 poll_interval: float = 2.0, now: Callable[[], float] = time.monotonic) -> None:
        self._sink = sink
        self.poll_interval = poll_interval
        self._now = now
        self._cpu = DiscState()
        self._mem = DiscState()
        self._bucket = BucketState(tokens=CAPACITY, last_refill=now())
        self._rollups: dict[str, RollupState] = {d: RollupState() for d in (watch_dirs or [])}
        self._overflow = 0

    def _admit(self, statement: str) -> None:
        self._bucket, ok = try_consume(self._bucket, self._now(), RATE, CAPACITY)
        if ok:
            self._sink(statement)
        else:
            self._overflow += 1  # backstop: coalesce, flushed as one event + logged (never silent)

    def poll_metrics(self) -> None:
        import psutil  # lazy

        self._cpu, cpu_emit = step(CPU_LADDER, self._cpu, psutil.cpu_percent())
        if cpu_emit:
            self._admit(signal_event("cpu", cpu_emit))
        self._mem, mem_emit = step(MEM_LADDER, self._mem, psutil.virtual_memory().percent)
        if mem_emit:
            self._admit(signal_event("mem", mem_emit))

    def on_fs_event(self, directory: str) -> None:
        if directory not in self._rollups:
            return  # allow-list salience filter
        self._rollups[directory], emit = on_event(self._rollups[directory], self._now())
        if emit:
            self._admit(activity_event(directory, emit))

    def tick_rollups(self) -> None:
        for directory, state in list(self._rollups.items()):
            self._rollups[directory], emit = on_tick(state, self._now())
            if emit:
                self._admit(activity_event(directory, emit))

    def flush_overflow(self) -> None:
        if self._overflow:
            self._sink(activity_event("sentinel", "overflow"))  # coalesced; never silent
            self._overflow = 0

    def run_once(self) -> None:
        self.poll_metrics()
        self.tick_rollups()
        self.flush_overflow()

    def watch(self):
        """Start a watchdog observer routing fs events to on_fs_event. Returns the observer."""
        from watchdog.events import FileSystemEventHandler  # lazy
        from watchdog.observers import Observer

        observer = Observer()
        outer = self
        for directory in self._rollups:
            class _Handler(FileSystemEventHandler):
                _dir = directory

                def on_any_event(self, event: object) -> None:
                    outer.on_fs_event(self._dir)

            observer.schedule(_Handler(), directory, recursive=True)
        observer.start()
        return observer
