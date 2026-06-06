"""Capstone M2: Mock sensor (pegged) -> Schmitt -> token bucket -> ONA (surprise trip)
-> fake LLM narration -> terminal alert. Deterministic; no real sensors or model."""
from brain import Brain
from sentinel import BucketState, CPU_LADDER, DiscState, signal_event, step, try_consume
from sentinel.narrate import Narrator
from sentinel.surprise import SurpriseDetector


class NarrateFake:
    """Deterministic stand-in for the GBNF-free narration LLM (observation-only)."""

    def generate(self, system_prompt: str, user: str) -> str:
        return "Observed anomaly: cpu is pegged. Context: it is usually not pegged. I am monitoring."


def test_m2_capstone() -> None:
    alerts: list = []
    with Brain(cycles_per_step=50) as brain:
        brain.add_belief("<cpu --> [pegged]>. {0.0 0.9}")  # strong prior: usually NOT pegged
        narrator = Narrator(NarrateFake(), on_alert=alerts.append)
        detector = SurpriseDetector(brain, threshold=0.5, on_surprise=narrator.narrate)

        # Mock sensor stream: pegged CPU -> Schmitt -> token bucket -> detector -> ONA -> surprise.
        cpu = DiscState(level=2, streak=2, emitted=2)  # already 'high'; the next pegged is the edge
        bucket = BucketState()
        emitted: list = []
        for sample in (90.0, 90.0):
            cpu, level = step(CPU_LADDER, cpu, sample)
            if level is None:
                continue
            statement = signal_event("cpu", level)
            bucket, admitted = try_consume(bucket, 0.0)
            if admitted:
                detector.observe(statement)
                emitted.append(statement)

    assert emitted == ["<cpu --> [pegged]>. :|:"], emitted
    assert len(alerts) == 1, alerts
    assert "monitoring" in alerts[0].lower()
    # Observation-only: no action wording survived to the terminal.
    assert not any(m in alerts[0].lower() for m in ("run ", "kill", "you should", "execute", "sudo"))


if __name__ == "__main__":
    test_m2_capstone()
    print("test_m2: OK (sensor -> schmitt -> bucket -> ONA surprise -> fake narration -> alert)")
