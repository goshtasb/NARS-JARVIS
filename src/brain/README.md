# brain

## Overview
The persistent symbolic reasoning core. Wraps the ONA (OpenNARS-for-Applications) C engine
as a bounded **L1 reasoning cache** (PRD §6). This is the only module that talks to the ONA
subprocess; every other module uses this public interface.

## Usage
```python
from brain import Brain

with Brain(cycles_per_step=100) as brain:
    brain.add_belief("<a --> b>.")
    brain.add_belief("<b --> c>.")
    print(brain.ask("<a --> c>?"))
    # Answer(term='<a --> c>', truth=Truth(1.0, 0.81), stamp=(2, 1), ...)  ← evidence trail
```
The NAR binary is located via `NARS_JARVIS_NAR_BIN`, else `../OpenNARS-for-Applications/NAR`.
Build it first: `(cd OpenNARS-for-Applications && sh build.sh)`.

## Key Components
- `ona.py` — `Brain`: the subprocess wrapper (Imperative Shell; all I/O).
- `parse.py` — pure parsers for ONA output (Functional Core; deterministic, no I/O).
- `__init__.py` — public interface (`Brain`, `Answer`, `Truth`, parsers).

## Dependencies
Python standard library only (`subprocess`, `pathlib`). No external packages.

## Tests
From `src/`:
- `python3 -m brain.test_parse` — unit (no binary needed).
- `python3 -m brain.test_ona_integration` — requires the built NAR binary.

## Related ADRs
ADR-001 (module boundaries / public interface); PRD §6 (two-tier memory).
