"""V2 Sentinel: dual-brain isolation, UTI-driven dynamic categorization, and the memoizer."""
import os
import shutil
import tempfile

from brain import Brain
from sentinel import SentinelStore, bucket_for_uti, build_sensor, classify


def test_dual_brain_isolation() -> None:
    # Two ONA instances = two processes = mathematically zero cross-contamination.
    with Brain(cycles_per_step=50) as knowledge, Brain(cycles_per_step=50) as sentinel:
        knowledge.add_belief("<tim --> duck>.")
        assert knowledge.ask("<tim --> duck>?") is not None          # knowledge has it
        assert sentinel.ask("<tim --> duck>?") is None               # sentinel never saw it
        sentinel.add_belief("<attention --> [thrashing]>. :|:")      # a behavioral event
        assert knowledge.ask("<attention --> [thrashing]>?") is None  # knowledge stays pristine


def test_classify_via_uti_then_override() -> None:
    # A novel app self-classifies from its own declared UTI — no static list, no LLM.
    assert classify("com.acme.brandnewtool", "public.app-category.developer-tools") == "dev"
    assert bucket_for_uti("public.app-category.social-networking") == "comms"
    # Override beats UTI where Apple has no/ambiguous category (browsers, comms apps).
    assert classify("com.google.Chrome", "public.app-category.productivity") == "web"
    assert classify("com.tinyspeck.slackmacgap", "public.app-category.business") == "comms"
    # No UTI + no override -> visible 'other' (residual, not dominant).
    assert classify("com.unknown.app", "") == "other"


def test_memoizer_caches_and_persists() -> None:
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        s1 = SentinelStore(db)
        assert s1.resolve("com.acme.tool", "public.app-category.developer-tools") == "dev"
        s1.set_override("com.unknown.app", "comms")
        s1.close()
        s2 = SentinelStore(db)                                   # reload from disk
        assert s2.resolve("com.acme.tool", "ignored-because-cached") == "dev"   # memoized
        assert s2.resolve("com.unknown.app") == "comms"          # override survived restart
        s2.close()
    finally:
        os.path.exists(db) and os.remove(db)


def test_sensor_compiles_when_swiftc_present() -> None:
    if shutil.which("swiftc") is None:
        print("SKIP: swiftc unavailable"); return
    binary = build_sensor()
    assert binary is not None and binary.exists(), "sensor.swift failed to compile"


def test_kpi_persists_and_computes_lift() -> None:
    # Focus blocks + interventions survive a restart and the lift readout is computed from them.
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        s1 = SentinelStore(db)
        s1.record_focus_block(500.0, 60.0)      # before the nudge
        s1.record_focus_block(1100.0, 300.0)    # after the nudge
        s1.record_intervention(1000.0, accepted=True)
        s1.close()
        s2 = SentinelStore(db)                   # reload from disk
        k = s2.kpi()
        assert k["accepted"] == 1
        assert k["pre_median_s"] == 60.0 and k["post_median_s"] == 300.0
        s2.close()
    finally:
        os.path.exists(db) and os.remove(db)


def test_calibration_scalars_only() -> None:
    # Burn-in is recorded ONCE; decline rate is the false-positive proxy. All numeric, no content.
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        s = SentinelStore(db)
        s.record_burnin(crossed_at=1000.0, elapsed_s=720.0, observations=6)
        s.record_burnin(crossed_at=2000.0, elapsed_s=99.0, observations=99)  # ignored (already set)
        s.record_intervention(1100.0, accepted=True)
        s.record_intervention(1200.0, accepted=False)
        c = s.calib()
        assert c["burnin_observations"] == 6 and c["burnin_elapsed_s"] == 720.0   # first only
        assert c["fired"] == 2 and c["declined"] == 1 and c["decline_rate"] == 0.5
        s.close()
    finally:
        os.path.exists(db) and os.remove(db)


if __name__ == "__main__":
    test_dual_brain_isolation()
    test_classify_via_uti_then_override()
    test_memoizer_caches_and_persists()
    test_sensor_compiles_when_swiftc_present()
    test_kpi_persists_and_computes_lift()
    test_calibration_scalars_only()
    print("test_sentinel_v2: OK")
