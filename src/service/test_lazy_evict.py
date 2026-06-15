"""Phase 1 (memory) — lazy-load + idle-eviction of the heavy conversational model. No real model: a stub
factory stands in for LocalLLM. Proves the model isn't built until first use, that it evicts after idle and
reloads on demand, that capability probes don't force a load, and — the safety crux — that eviction acquires
the inference lock so it can NEVER race an in-flight decode."""
import threading
import time

from service.local_brain import LocalBrain
from service.wiring import LazyLLM


class _StubLLM:
    def __init__(self):
        self.calls = 0
    def generate_text(self, system, user, max_tokens=64):
        self.calls += 1
        return "ok"


# ── LazyLLM: lazy build, capability transparency, evict + reload ──
def test_lazyllm_builds_on_first_use_only() -> None:
    built = []
    lz = LazyLLM(lambda: built.append(1) or _StubLLM())
    assert not lz.loaded and built == []                 # constructing the wrapper loads nothing
    assert hasattr(lz, "generate_text")                  # capability is visible while unloaded …
    assert built == []                                   # … and probing it did NOT load the model
    assert lz.generate_text("s", "u") == "ok"            # first real call loads
    assert lz.loaded and len(built) == 1


def test_lazyllm_evict_then_reload() -> None:
    built = []
    lz = LazyLLM(lambda: built.append(1) or _StubLLM())
    lz.generate_text("s", "u"); assert lz.loaded and len(built) == 1
    lz.evict(); assert not lz.loaded                     # weights dropped
    lz.generate_text("s", "u"); assert lz.loaded and len(built) == 2   # transparently reloaded


# ── LocalBrain idle-eviction policy ──
class _FakeModel:
    def __init__(self):
        self._loaded = True; self.evicts = 0
    @property
    def loaded(self):
        return self._loaded
    def evict(self):
        self._loaded = False; self.evicts += 1
    def generate_text(self, system, user, max_tokens=64):
        return "ok"


def test_evicts_after_idle_threshold() -> None:
    fm = _FakeModel()
    lb = LocalBrain(fm, idle_evict_s=0.0)                # idle threshold 0 -> immediately eligible
    try:
        lb._maybe_evict()
        assert not fm.loaded and fm.evicts == 1
    finally:
        lb.close()


def test_does_not_evict_while_busy() -> None:
    fm = _FakeModel()
    lb = LocalBrain(fm, idle_evict_s=0.0)
    try:
        lb._busy = True                                  # an async decode is in flight
        lb._maybe_evict()
        assert fm.loaded and fm.evicts == 0
    finally:
        lb.close()


def test_does_not_evict_when_recently_used() -> None:
    fm = _FakeModel()
    lb = LocalBrain(fm, idle_evict_s=300.0)              # 5 min threshold, just-now last_use
    try:
        lb._maybe_evict()
        assert fm.loaded and fm.evicts == 0
    finally:
        lb.close()


def test_no_op_when_already_unloaded_or_demo_source() -> None:
    fm = _FakeModel(); fm._loaded = False
    lb = LocalBrain(fm, idle_evict_s=0.0)
    try:
        lb._maybe_evict(); assert fm.evicts == 0        # nothing loaded -> no-op
    finally:
        lb.close()

    class _Demo:                                          # no loaded/evict attrs (DemoClaims-like)
        def generate(self, *a): return "[]"
    lb2 = LocalBrain(_Demo(), idle_evict_s=0.0)
    try:
        lb2._maybe_evict()                               # must not raise
    finally:
        lb2.close()


def test_eviction_cannot_race_the_inference_lock() -> None:
    """The safety crux: while the inference lock is held (a decode in progress), eviction must BLOCK and not
    fire; once the lock frees it proceeds. Proves eviction can never unload mid-decode."""
    fm = _FakeModel()
    lb = LocalBrain(fm, idle_evict_s=0.0)
    try:
        lb._lock.acquire()                               # simulate an in-flight decode holding the context
        done = threading.Event()
        threading.Thread(target=lambda: (lb._maybe_evict(), done.set()), daemon=True).start()
        assert not done.wait(0.3)                        # blocked on the lock -> did NOT evict mid-decode
        assert fm.loaded and fm.evicts == 0
        lb._lock.release()                               # "decode" completes
        assert done.wait(1.0)                            # eviction now proceeds
        assert not fm.loaded and fm.evicts == 1
    finally:
        if lb._lock.locked():
            lb._lock.release()
        lb.close()


def test_close_stops_the_evictor_thread() -> None:
    lb = LocalBrain(_FakeModel(), idle_evict_s=300.0)
    assert lb._evictor.is_alive()
    lb.close()
    time.sleep(0.05)
    assert not lb._evictor.is_alive()                    # joined, no leaked thread
