"""ADR-036 persona ingestion loop: idle-gated batch → extract → feed ONA → write-through → consume,
and fail-closed when the persona ONA is unrecoverable. Fake brain + fake generate (no real ONA/model)."""
import types

from brain import BrainUnavailable
from persona import PersonaStore
from service.persona_loop import PersonaLoop

_GEN = lambda s, u, mt: '[{"predicate":"format_directive","value":"omit_greeting_prose","freq":1.0,"conf":0.9}]'


class _FakeBrain:
    """Records fed beliefs and answers their truth back (so write-through has something to read)."""
    def __init__(self, fail: bool = False):
        self.fed: list[str] = []
        self._t: dict[str, tuple[float, float]] = {}
        self._fail = fail
    def add_belief(self, narsese: str):
        if self._fail:
            raise BrainUnavailable("simulated NAR collapse")
        self.fed.append(narsese)
        term, tv = narsese.split(". {")
        f, c = tv.rstrip("}").split()
        self._t[term.strip()] = (float(f), float(c))
    def ask(self, q: str):
        t = q.rstrip("?").strip()
        if t not in self._t:
            return None
        f, c = self._t[t]
        return types.SimpleNamespace(truth=types.SimpleNamespace(frequency=f, confidence=c))


def test_idle_gated_does_not_drain_when_active() -> None:
    s = PersonaStore(":memory:"); s.buffer_event("no preamble please")
    loop = PersonaLoop(_FakeBrain(), s, _GEN)
    loop.tick(idle=False, overnight_active=False)            # busy -> must not touch the buffer
    assert s.pending_count() == 1


def test_idle_batch_feeds_brain_writes_through_and_consumes() -> None:
    s = PersonaStore(":memory:"); s.buffer_event("just give me the answer, no greeting")
    b = _FakeBrain()
    loop = PersonaLoop(b, s, _GEN)
    loop.tick(idle=True)
    assert s.pending_count() == 0                            # consumed
    assert any("omit_greeting_prose" in x for x in b.fed)    # fed to the persona ONA
    assert any("omit_greeting_prose" in r["term"] for r in s.current(0.75))  # checkpointed for injection
    assert "Omit greetings" in str([r for r in s.current(0.75)]) or loop.persona()  # injectable


def test_fail_closed_disables_injection_on_brain_unavailable() -> None:
    s = PersonaStore(":memory:"); s.buffer_event("x")
    logs = []
    loop = PersonaLoop(_FakeBrain(fail=True), s, _GEN, emit=lambda k, b: logs.append(b.get("text", "")))
    loop.tick(idle=True)
    assert loop.down and loop.persona() == []               # stateless, injection disabled
    assert any("COGNITIVE LAYER ERROR" in m for m in logs)  # logged once


def test_replay_refeeds_checkpoint_on_construct() -> None:
    s = PersonaStore(":memory:")
    s.upsert_concept("<current_focus --> local_development>", 1.0, 0.9)
    b = _FakeBrain()
    PersonaLoop(b, s, _GEN)                                  # __init__ replays the checkpoint
    assert any("local_development" in x for x in b.fed)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("service/test_persona_loop: OK")
