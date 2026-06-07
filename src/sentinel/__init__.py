"""sentinel — local observation pipeline + curiosity (C3 / M2). Observe-only.

Pure cores (schmitt / rollup / limiter / narsese) carry the flooding-prevention math; the
SurpriseDetector wraps ONA for prediction-divergence; the Narrator turns surprises into
action-forbidden alerts. `SystemSentinel` is the thin psutil/watchdog shell. Public interface
(ADR-001).
"""
from .limiter import BucketState, try_consume
from .narrate import NARRATION_SYSTEM_PROMPT, Narrator, sanitize_narration
from .narsese import activity_event, signal_event
from .rollup import RollupState, on_event, on_tick
from .schmitt import CPU_LADDER, MEM_LADDER, DiscState, Ladder, step
from .fragmentation import FRAGMENTATION_LADDER, WINDOW, RingState, rate, record
from .sensor import BUCKETS, Sensor, bucket_for_uti, build_sensor, classify
from .sensors import SystemSentinel
from .store import SentinelStore
from .surprise import SurpriseDetector, SurpriseEvent, expectation

__all__ = [
    "Ladder",
    "DiscState",
    "step",
    "CPU_LADDER",
    "MEM_LADDER",
    "RollupState",
    "on_event",
    "on_tick",
    "BucketState",
    "try_consume",
    "signal_event",
    "activity_event",
    "SurpriseDetector",
    "SurpriseEvent",
    "expectation",
    "Narrator",
    "sanitize_narration",
    "NARRATION_SYSTEM_PROMPT",
    "SystemSentinel",
    "Sensor",
    "build_sensor",
    "classify",
    "bucket_for_uti",
    "BUCKETS",
    "SentinelStore",
    "RingState",
    "record",
    "rate",
    "FRAGMENTATION_LADDER",
    "WINDOW",
]
