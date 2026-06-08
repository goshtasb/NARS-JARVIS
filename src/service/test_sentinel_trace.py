"""Unit tests for sentinel observability formatters (ADR-016). Pure."""
from service.sentinel_trace import format_gate_proximity, format_observation


def test_format_observation_has_numbers_no_content() -> None:
    line = format_observation("comms", "fragmented", 0.41, 0.62, 0.21, 0.78, armed=False)
    for tok in ("cat=comms", "level=fragmented", "surprise=0.41", "prior_exp=0.62",
                "actual_exp=0.21", "prior_conf=0.78", "armed=False"):
        assert tok in line
    # no app id / bundle / title leaks
    assert ".app" not in line and "com." not in line


def test_format_observation_no_prior() -> None:
    line = format_observation("dev", "focused", 0.0, None, 0.5, 0.0, armed=False)
    assert "prior_exp=n/a" in line


def test_format_gate_proximity_arming_and_delta() -> None:
    out = format_gate_proximity([("comms", 0.62), ("media", 0.90)])
    assert "comms E=0.62" in out and "to-arm" in out
    assert "media E=0.90" in out and "ARMED" in out


def test_format_gate_proximity_empty() -> None:
    assert "no learned categories" in format_gate_proximity([])


if __name__ == "__main__":
    test_format_observation_has_numbers_no_content()
    test_format_observation_no_prior()
    test_format_gate_proximity_arming_and_delta()
    test_format_gate_proximity_empty()
    print("service/test_sentinel_trace: OK")
