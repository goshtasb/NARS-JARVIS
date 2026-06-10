# ADR-037: Persona introspection & control (the Cognitive Identity glass box)

## Status
Accepted & live-verified. Makes the ADR-036 persona layer auditable + correctable — the same glass-box
+ delete guarantee ADR-027 gave the Habit Brain. Suite 464 → **468**. Tagged **v1.11.0**.

## Context
ADR-036 shipped a persona layer that silently shapes *every* answer from a hidden SQLite table. An
autonomous learner that steers all output must be inspectable and reversible: if it locks in a wrong
high-confidence constraint (e.g. "always raw JSON"), the user needs to *see* it and *delete* it before
it pollutes the Morning Briefing. Control precedes optimization (recall tuning is the next cycle).

## Decision
Mirror ADR-027 exactly — route new data into the existing dashboard, no new view/loop/menu item.

- **`persona/store.py`** — `delete(term)` (one-row purge).
- **`service/persona_loop.py`**:
  - `snapshot() -> [{term, phrase, confidence, state}]` — O(1) read of all checkpointed concepts, each
    translated to its plain-English phrase via `vocab.phrase_for`; `state = "Active"` (conf ≥ 0.75 inject
    floor) else `"Learning"`. No NARS math leaks to the UI. `[]` when the layer is down.
  - `forget(term)` — `store.delete(term)` **and** crater the belief in the **isolated persona ONA**
    (`add_belief("<…>. {0.0 0.9}")`), so SQLite and the live reasoner stay in sync (exact mirror of
    `HabitLoop.forget`). The DB delete removes it from injection; the crater stops it re-arming this session.
- **`service/session.py`** — dispatch `persona_list` → `{rows}` and `persona_forget` (term) → `{text}`.
- **`ui/HabitsView.swift`** — the popover is retitled **"🧠 Cognitive Identity"** and split into two
  fixed sub-stacks: **Routine Cadence** (the existing habit rows) and **Persona Constraints** (each
  constraint's phrase + `[Active]`/`[Learning]` badge + a red **Forget** → `persona_forget <term>`).
  Two sub-stacks so the two async fetches render into their own regions (no ordering race). The
  right-click menu item is renamed to match; no new popover/loop.

## Consequences
- **Gained:** the persona layer is a glass box — the user sees every learned style/focus constraint and
  can sever any one with a click; the deletion propagates to both SQLite and the live persona ONA.
- **Live-verified:** seeding `omit_greeting_prose` → `persona_list` shows it `[Active]`; `persona_forget`
  removes the row AND feeds `{0.0 0.9}` to the isolated persona NAR (belief collapses); the dashboard
  renders both sections; the app builds + signs stably.
- **Tests:** +4 — `store.delete`; `snapshot` (Active/Learning + phrase, no raw freq leak); `forget`
  (purge + crater); dispatch `persona_list`/`persona_forget` (stub-bound, ADR-030 technique). Suite **468**.
- **Safety unchanged:** introspection/forget never touch the action firewall; persona still only shapes
  the prompt. Forget is safe + reversible (JARVIS re-learns if the pattern recurs, like habit forget).

## Alternatives Considered
- **A separate persona menu-bar view:** rejected — the user's cognitive identity belongs in one pane;
  added a section to the existing dashboard instead.
- **Raw DB delete without cratering ONA:** rejected — would desync the live reasoner from SQLite; the
  belief could re-inject before restart. Crater + delete keeps them consistent.
