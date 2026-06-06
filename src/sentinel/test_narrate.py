"""Narration tests with CHAOTIC fakes: action hallucinations / malformed / crashes -> fallback."""
from sentinel.narrate import Narrator, sanitize_narration
from sentinel.surprise import SurpriseEvent

_EVENT = SurpriseEvent("<cpu --> [pegged]>", "<cpu --> [pegged]>. :|:", 0.05, 0.95, 0.9)


class CompliantFake:
    def generate(self, system_prompt: str, user: str) -> str:
        return "Observed anomaly: cpu pegged. Context: usually idle. I am monitoring."


class ActionHallucinationFake:
    def generate(self, system_prompt: str, user: str) -> str:
        return "You should run `kill -9 1234` and restart the service immediately."


class MalformedFake:
    def generate(self, system_prompt: str, user: str) -> str:
        return ""


class CrashingFake:
    def generate(self, system_prompt: str, user: str) -> str:
        raise RuntimeError("model exploded")


def test_sanitize_rejects_actions_and_malformed() -> None:
    assert sanitize_narration("Observed anomaly: x. I am monitoring.") is not None
    assert sanitize_narration("You should run kill -9") is None
    assert sanitize_narration("") is None
    assert sanitize_narration(None) is None


def test_compliant_passes_through() -> None:
    alerts: list = []
    Narrator(CompliantFake(), on_alert=alerts.append).narrate(_EVENT)
    assert len(alerts) == 1 and "monitoring" in alerts[0].lower()


def test_action_hallucination_falls_back() -> None:
    alerts: list = []
    out = Narrator(ActionHallucinationFake(), on_alert=alerts.append).narrate(_EVENT)
    assert "kill" not in out.lower() and "run " not in out.lower()
    assert "monitoring" in out.lower()  # generic, observation-only fallback


def test_malformed_and_crash_fall_back() -> None:
    for fake in (MalformedFake(), CrashingFake()):
        alerts: list = []
        out = Narrator(fake, on_alert=alerts.append).narrate(_EVENT)  # must not raise
        assert "anomaly" in out.lower() and len(alerts) == 1


if __name__ == "__main__":
    test_sanitize_rejects_actions_and_malformed()
    test_compliant_passes_through()
    test_action_hallucination_falls_back()
    test_malformed_and_crash_fall_back()
    print("sentinel/test_narrate: OK")
