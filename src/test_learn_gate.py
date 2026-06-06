"""Batch-and-queue ingestion (the directive's compound-statement case).

"Tim is a duck, coffee makes me alert, and a car is fast." -> three claims:
  1. <tim --> duck>            : L0 COMMIT
  2. <(coffee*me_alert)--makes>: L0 REJECT (non-taxonomic verb)
  3. PropertyClaim(automobile) : L0 DEFER -> L1 ESCALATE (controlled cosine 0.85)
Proves: evaluate-all-first, Phase 1 rejects BEFORE Phase 2 escalations, Phase 3 commits only the
final set, escalation y commits / n skips, single summary, no exceptions.
"""
import json
import math

from brain import Brain
from jarvis import Jarvis
from language import IngestionGate, Translator
from memory import MemoryStore

SENT = "Tim is a duck, coffee makes me alert, and a car is fast."
_MIRROR = "automobile is fast."                 # back_render of the escalated claim
_VB = [0.85, math.sqrt(1.0 - 0.85 ** 2)]        # cosine 0.85 vs the sentence vector -> ESCALATE


class _MultiLLM:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return json.dumps([
            {"type": "RelationClaim", "subject": "Tim", "verb": "is_a", "object": "duck"},
            {"type": "RelationClaim", "subject": "coffee", "verb": "makes", "object": "me alert"},
            {"type": "PropertyClaim", "subject": "automobile", "value": "fast"},
        ])


class _FakeEmb:
    def embed(self, text: str):
        return {SENT: [1.0, 0.0], _MIRROR: _VB}[text]


def _run(confirm_yes: bool):
    log: list = []
    def on_rejects(items): log.append(("reject", [i.english_mirror for i in items]))
    def confirm(item): log.append(("confirm", item.english_mirror)); return confirm_yes
    with Brain(cycles_per_step=50) as brain:
        store = MemoryStore()
        jarvis = Jarvis(Translator(_MultiLLM()), store, brain, gate=IngestionGate(_FakeEmb()))
        committed = jarvis.learn(SENT, on_rejects=on_rejects, confirm_escalation=confirm)
        return committed, log, store.count()


def test_phase_order_and_bucketing() -> None:
    _, log, _ = _run(confirm_yes=True)
    assert [k for k, _ in log] == ["reject", "confirm"], log   # Phase 1 strictly before Phase 2
    assert log[0][1] == ["coffee makes me alert."]             # causal claim bounced at L0
    assert log[1][1] == _MIRROR                                # ambiguous claim escalated


def test_escalation_yes_commits() -> None:
    committed, _, count = _run(confirm_yes=True)
    assert any("tim --> duck" in s for s in committed), committed
    assert any("automobile --> [fast]" in s for s in committed), committed
    assert count >= 2


def test_escalation_no_skips() -> None:
    committed, _, _ = _run(confirm_yes=False)
    assert any("tim --> duck" in s for s in committed), committed
    assert not any("automobile" in s for s in committed), committed


if __name__ == "__main__":
    test_phase_order_and_bucketing()
    test_escalation_yes_commits()
    test_escalation_no_skips()
    print("test_learn_gate: OK")
