"""V2 Sentinel scaffold: dual-brain isolation, app->category mapping, and the sensor build."""
import shutil

from brain import Brain
from sentinel import build_sensor, category


def test_dual_brain_isolation() -> None:
    # Two ONA instances = two processes = mathematically zero cross-contamination.
    with Brain(cycles_per_step=50) as knowledge, Brain(cycles_per_step=50) as sentinel:
        knowledge.add_belief("<tim --> duck>.")
        assert knowledge.ask("<tim --> duck>?") is not None          # knowledge has it
        assert sentinel.ask("<tim --> duck>?") is None               # sentinel never saw it
        sentinel.add_belief("<editor --> [foreground]>. :|:")        # a behavioral event
        assert knowledge.ask("<editor --> [foreground]>?") is None   # knowledge stays pristine


def test_category_map_is_coarse_and_closed() -> None:
    assert category("com.microsoft.VSCode") == "editor"
    assert category("com.todesktop.230313mzl4w4u92") == "editor"     # Cursor (verified live)
    assert category("com.google.Chrome") == "browser"
    assert category("com.tinyspeck.slackmacgap") == "comms"
    assert category("com.apple.Terminal") == "terminal"
    assert category("com.some.unknown.app") == "other"               # default, low-cardinality


def test_sensor_compiles_when_swiftc_present() -> None:
    if shutil.which("swiftc") is None:
        print("SKIP: swiftc unavailable"); return
    binary = build_sensor()
    assert binary is not None and binary.exists(), "sensor.swift failed to compile"


if __name__ == "__main__":
    test_dual_brain_isolation()
    test_category_map_is_coarse_and_closed()
    test_sensor_compiles_when_swiftc_present()
    print("test_sentinel_v2: OK")
