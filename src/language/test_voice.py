"""Outbound voice — deterministic truth->band mapping, template, and the ruthless sanitizer."""
from language.voice import (
    Band,
    Polarity,
    Verdict,
    Voice,
    assess,
    deterministic_answer,
    sanitize_voice,
)

_V = Verdict(Polarity.YES, Band.TENTATIVE, "Tim is a bird", 0.65, 1.0,
             ["Tim is a duck", "ducks are birds"])


def test_assess_bands() -> None:
    assert assess(1.0, 0.95) == (Polarity.YES, Band.CONFIDENT)
    assert assess(1.0, 0.65) == (Polarity.YES, Band.TENTATIVE)   # the directive's example
    assert assess(0.0, 0.9) == (Polarity.NO, Band.CONFIDENT)
    assert assess(0.5, 0.9) == (Polarity.UNCLEAR, Band.CONFIDENT)
    assert assess(1.0, 0.4) == (Polarity.YES, Band.GUESS)


def test_deterministic_template_carries_truth_and_evidence() -> None:
    s = deterministic_answer(_V)
    assert s.startswith("Tentatively, yes — Tim is a bird.")
    assert "confidence 0.65" in s
    assert "Tim is a duck; ducks are birds" in s


def test_sanitizer_accepts_faithful_prose() -> None:
    # tentative band: a hedge must remain; content words (tim, bird) are in the evidence base.
    assert sanitize_voice("Tim is a bird, but I'm not sure", _V) is not None
    assert sanitize_voice("Tentatively, Tim is a bird", _V) is not None


def test_sanitizer_rejects_upgraded_certainty() -> None:
    # "probably" overstates a TENTATIVE verdict -> reject (no tentative marker present).
    assert sanitize_voice("Tim is probably a bird", _V) is None


def test_sanitizer_kills_hallucinated_noun() -> None:
    assert sanitize_voice("Tim is a penguin, but I'm not sure", _V) is None   # penguin not in base


def test_sanitizer_kills_dropped_certainty_marker() -> None:
    # Tentative band requires a 'tentativ'/hedge marker; flat assertion drops it.
    assert sanitize_voice("Tim is a bird", _V) is None


def test_sanitizer_kills_overlong() -> None:
    assert sanitize_voice("Tim is a bird " * 50, _V) is None


def test_voice_falls_back_to_template_on_bad_formatter() -> None:
    class Hallucinator:
        def generate_text(self, system, user): return "Actually Tim is a famous penguin from Antarctica."
    out = Voice(Hallucinator()).say(_V)
    assert out == deterministic_answer(_V)          # silently replaced, no error surfaced


def test_voice_uses_clean_prose_plus_trail() -> None:
    class Good:
        def generate_text(self, system, user): return "Tentatively, Tim is a bird"
    out = Voice(Good()).say(_V)
    assert out.startswith("Tentatively, Tim is a bird") and "confidence 0.65" in out


def test_voice_template_only_without_formatter() -> None:
    assert Voice().say(_V) == deterministic_answer(_V)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("language/test_voice: OK")
