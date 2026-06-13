"""summaries — the ADR-058 durable archive of briefed document summaries.

Every Canvas/overnight `summarize_file` result is appended here (text, owned by the daemon so it
survives the app being closed). The Swift client materializes each record into an openable PDF.

Public interface (ADR-001: a module's surface is its `__init__.py` + `__all__`).
"""
from .store import SummaryArchive

__all__ = ["SummaryArchive"]
