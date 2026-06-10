"""research — the bounded agentic web-research loop (ADR-039).

Public interface (S-01): `run_research` is the single entry point; the pure parsers are exported for
the test suite and any future caller that needs to interpret loop artifacts.
"""
from .agent import links_from_results, parse_step, run_research, split_browse

__all__ = ["run_research", "parse_step", "links_from_results", "split_browse"]
