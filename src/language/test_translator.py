"""Boundary tests for the Translator with CHAOTIC fakes (no model).

Per directive: the fake LLM returns malformed / schema-violating output that GBNF should have
blocked. We verify the pipeline cleanly REJECTS and ALERTS the sentinel hook — never crashes.
"""
from language.translator import Translator


class CompliantFake:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return '[{"type":"RelationClaim","subject":"Tim","verb":"IsA","object":"duck"}]'


class MalformedJSONFake:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return '[{"type":"RelationClaim", "subject":'  # truncated -> invalid JSON


class SchemaViolationFake:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return '[{"type":"BogusClaim","subject":"x"}]'  # unknown claim type


class MissingFieldFake:
    def generate(self, system_prompt: str, sentence: str) -> str:
        return '[{"type":"RelationClaim","subject":"x"}]'  # missing verb/object


def _capture() -> tuple[list, "object"]:
    alerts: list[tuple[str, str]] = []
    return alerts, (lambda sentence, error: alerts.append((sentence, error)))


def test_compliant_passes() -> None:
    result = Translator(CompliantFake()).translate("Tim is a duck.")
    assert result.ok and result.narsese == ["<tim --> duck>."], result


def test_malformed_json_rejected_and_alerts() -> None:
    alerts, hook = _capture()
    result = Translator(MalformedJSONFake(), on_reject=hook).translate("garbage in")
    assert result.ok is False and result.error is not None and result.narsese == []
    assert len(alerts) == 1 and alerts[0][0] == "garbage in"


def test_schema_violation_rejected_and_alerts() -> None:
    alerts, hook = _capture()
    result = Translator(SchemaViolationFake(), on_reject=hook).translate("bad type")
    assert result.ok is False and len(alerts) == 1


def test_missing_field_rejected_and_alerts() -> None:
    alerts, hook = _capture()
    result = Translator(MissingFieldFake(), on_reject=hook).translate("missing field")
    assert result.ok is False and len(alerts) == 1


if __name__ == "__main__":
    test_compliant_passes()
    test_malformed_json_rejected_and_alerts()
    test_schema_violation_rejected_and_alerts()
    test_missing_field_rejected_and_alerts()
    print("language/test_translator: OK")
