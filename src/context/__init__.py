"""context — dynamic situational context (ADR-010): fresh live facts injected each turn, and the
volatile-fact guard that keeps transient statements out of durable memory.

The LLM has no clock or system awareness; rather than rely on local tool-calling, the daemon injects
a small live-context block (date/time + system snapshot + optional sentinel foreground) every turn.
Public interface (ADR-001).
"""
from .providers import clock_fact, foreground_fact, render_live_context, system_fact
from .volatile import is_volatile

__all__ = [
    "render_live_context",
    "clock_fact",
    "system_fact",
    "foreground_fact",
    "is_volatile",
]
