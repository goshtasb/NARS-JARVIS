# ADR-027: Habit introspection & pruning (observability for the Habit Brain)

## Status
Accepted & live-verified. Makes the ADR-026 learning brain auditable + controllable. Suite 386 → **396**.
Tagged **v1.3.0**.

## Context
ADR-026 gave JARVIS a brain that silently accumulates habit beliefs. Trust in an autonomous system
requires **observability + control**: the user must see exactly what's been learned and delete any of
it. Two new actions: `list_habits` (read) and `forget_habit` (prune).

## Decision
- **Representation rule:** the NARS math is **fully encapsulated in Python**; the action returns a
  finished, human-readable string the LLM only *relays* (like `report_system`/`find_file`). The 7B
  never sees raw `w`/`c`/`E` — it would misnarrate NAL (confidence ≠ probability). State is computed by
  the **same `gate_passes`** the proposal tick uses (one source of truth):
  - `[Armed]` — gated ("I may offer this").
  - `[Learning]` — below the gate, with an honest **count** `evidence_count = round(c/(1-c))` →
    *"seen ~4×, arms at ~6"* — never a percentage.
- **`habits/quantize.py` (pure):** `bucket_label` (h09→"9:00 AM"), `evidence_count`, `describe_habit`
  ("mute around 2:00 PM").
- **`habits/store.py`:** `list_all()` (full rows), `delete(key)`.
- **`service/habit_loop.py`:** `describe()` (finished list w/ state+count) and `forget(query)` — match by
  exact key or substring (key/action/arg/description); for each match **crater the term** (`{0.0 0.9}`
  absolute negative, so it won't re-arm if re-seen before restart) **and** delete the row.
- **`actions/catalog.py`:** `list_habits` / `forget_habit` as new kind `"habit"` — listed in the prompt,
  **not** `eligible()` (so asking about habits never *becomes* a habit). Frictionless (no consent;
  deleting learned state is safe + reversible — JARVIS re-learns if the behaviour recurs).
- **`jarvis`/`session`:** `habit_admin` DI; `_run_actions` routes `kind=="habit"` →
  `session._habit_admin` → `HabitLoop.describe()` / `.forget(arg)`.

## Consequences
- **Gained:** the learning brain is a glass box — "what habits are you tracking?" and "forget the mute
  habit" both work, with no raw math exposed.
- **Live-verified (real daemon):** two `mute` requests → *"mute around 4:00 PM — [Learning] (seen ~2×,
  arms at ~6)"*; in-process, an armed habit shows `[Armed]`, `forget` removes it (cratered + purged).
- **Tests:** +10 — quantize (`bucket_label`/`evidence_count`/`describe_habit`), store (`list_all`/
  `delete`), habit_loop (`describe` armed/learning/empty + **no numeric leak**, `forget` crater+delete/
  no-match), catalog (kind + not-eligible), converse routing. Suite **396**.
- **Honest limits:**
  - **LLM relays, never interprets** — enforced by returning a finished string with no numbers in it.
  - **`forget` is substring-matched** — a broad query forgets all matches and lists them (transparent);
    exact key wins.
  - Still surfaces **Phase-1 (temporal)** habits; Phase 2 (weekday/foreground) will enrich descriptions.

## Alternatives Considered
- **Expose raw `w`/`c`/`E` to the LLM:** rejected — the 7B would invent wrong NAL explanations ("75%
  confident" misreads confidence-as-probability), violating the factual mandate.
- **Consent-gate `forget_habit`:** rejected — it only deletes *learned state* (safe, reversible); a
  confirmation prompt would be friction without safety gain.
