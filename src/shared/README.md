# shared

## Overview
Cross-cutting utilities used across domains (the S-01 shared kernel). Pure, dependency-free.

## Contents
- `text.py` — `atom(name)`: sanitize a string into a valid Narsese atom (`[a-z0-9_]`, spaces →
  underscores; empty → `_`). Used by `language/` (claim → Narsese) and `sentinel/` (event builders).

## Usage
```python
from shared import atom
atom("Obj Dir!")  # -> "obj_dir"
```

## Tests
From `src/`: `python3 -m shared.test_text`.

## Related
S-01 (shared infrastructure / promotion rule); ADR-001 (module boundaries).
