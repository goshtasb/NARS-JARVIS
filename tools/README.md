# tools

## Overview
Standalone operational instruments. Nothing here runs inside the daemon or is imported by `src/` —
each tool is a separate process that observes or measures, so running one never mutates assistant
state (and is therefore safe during a field-test freeze).

## The tools
- **`overnight_monitor.py`** — the field-test instrument (ADR-031/032): samples the daemon's RSS/CPU,
  system memory, and (best-effort) thermal pressure on an interval and appends a CSV
  (default `$TMPDIR/jarvis_overnight_monitor.csv`). Read-only observer; a PID change in the CSV is a
  daemon restart, so the telemetry is self-auditing. The morning question it answers: did the model
  leak, did the machine choke, did the daemon survive?
  `nohup python3 tools/overnight_monitor.py &`
- **`overnight_coder_feasibility.py`** — the pre-ADR-043 measurement harness: feeds 10 of this repo's
  pure functions to a local GGUF with a distilled few-shot TDD prompt, executes the generated pytest
  files in a throwaway cwd (NOT an OS sandbox — documented limit; supervised use only), and reports
  generated/runnable/passed/assert-quality. Measured 2026-06-10: conversational 7B 57% per-test-function
  green vs **qwen2.5-coder-7b 81%** — the number that gates the overnight-coder design.
  `python3 tools/overnight_coder_feasibility.py models/<model>.gguf`

## Related ADRs
ADR-031/032 (the overnight pipeline the monitor instruments); ADR-043 (planned — gated on the
feasibility numbers above and a real OS execution sandbox).
