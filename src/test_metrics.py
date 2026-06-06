"""Local ingestion telemetry: rejection-rate decay + the privacy guarantee (no text, ever)."""
import json
import math
import os
import tempfile

from brain import Brain
from jarvis import Jarvis
from language import IngestionGate, Translator
from memory import MemoryStore, MetricsStore


def test_schema_stores_no_text_columns() -> None:
    m = MetricsStore(":memory:", "s")
    cols = {row[1] for row in m._db.execute("PRAGMA table_info(ingestion_metrics)").fetchall()}
    assert cols == {"id", "timestamp", "session_id", "layer", "outcome"}, cols
    forbidden = {"english", "narsese", "sentence", "input", "text", "raw", "claim"}
    assert not (cols & forbidden), "telemetry must never have a text column"
    m.close()


def test_rates_and_decay_session_vs_prior() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        prior = MetricsStore(db, "older")    # prior session: 2/4 rejected = 50%
        prior.record_batch([("L0", "COMMIT_CLEAN"), ("L0", "COMMIT_CLEAN"),
                            ("L0", "REJECT_STRUCTURAL"), ("L1", "REJECT_SEMANTIC")])
        prior.close()
        cur = MetricsStore(db, "current")     # current session: 1/4 rejected = 25% -> decaying
        cur.record_batch([("L0", "COMMIT_CLEAN"), ("L0", "COMMIT_CLEAN"),
                          ("L1", "ESCALATE_ACCEPTED"), ("L0", "REJECT_FUSED")])
        s = cur.summary()
        assert s["total"] == 8
        assert abs(s["session_rate"] - 0.25) < 1e-9
        assert abs(s["prior_rate"] - 0.50) < 1e-9        # healthy decay: 25% < 50%
        assert s["taxonomy"]["COMMIT_CLEAN"] == 4
        cur.close()
    finally:
        os.path.exists(db) and os.remove(db)


# ── end-to-end: learn records outcomes, and NO user text reaches the table ──
SENT = "Tim is a duck, coffee makes me alert, and a car is fast."
_MIRROR, _VB = "automobile is fast.", [0.85, math.sqrt(1 - 0.85 ** 2)]


class _MultiLLM:
    def generate(self, system_prompt, sentence):
        return json.dumps([
            {"type": "RelationClaim", "subject": "Tim", "verb": "is_a", "object": "duck"},
            {"type": "RelationClaim", "subject": "coffee", "verb": "makes", "object": "me alert"},
            {"type": "PropertyClaim", "subject": "automobile", "value": "fast"},
        ])


class _FakeEmb:
    def embed(self, text): return {SENT: [1.0, 0.0], _MIRROR: _VB}[text]


def test_learn_records_gate_outcomes_without_text() -> None:
    with Brain(cycles_per_step=50) as brain:
        metrics = MetricsStore(":memory:", "sess")
        j = Jarvis(Translator(_MultiLLM()), MemoryStore(), brain,
                   gate=IngestionGate(_FakeEmb()), metrics=metrics)
        j.learn(SENT, on_rejects=lambda items: None, confirm_escalation=lambda item: True)
        tax = metrics.summary()["taxonomy"]
        assert tax.get("COMMIT_CLEAN") == 1          # Tim is a duck
        assert tax.get("REJECT_STRUCTURAL") == 1     # coffee makes ... (non-taxonomic verb)
        assert tax.get("ESCALATE_ACCEPTED") == 1     # automobile, confirmed
        # PRIVACY: not a single stored value may contain the user's words.
        cells = [str(c) for row in metrics._db.execute("SELECT * FROM ingestion_metrics").fetchall()
                 for c in row]
        for leak in ("Tim", "duck", "coffee", "automobile", "car", SENT):
            assert not any(leak in c for c in cells), f"text leaked into telemetry: {leak!r}"
        metrics.close()


if __name__ == "__main__":
    test_schema_stores_no_text_columns()
    test_rates_and_decay_session_vs_prior()
    test_learn_records_gate_outcomes_without_text()
    print("test_metrics: OK")
