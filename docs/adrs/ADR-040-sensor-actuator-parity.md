# ADR-040: Sensor–actuator parity & scope-honest reports

## Status
Accepted & live-verified. Extends ADR-019 (the action catalog) and the v1.8.2 deterministic gating.
Suite 479 → **483**.

## Context
A deliberate user probe — *"Can you check and see why the volume button on my computer doesn't
work?"* — produced a CPU/memory/disk/battery report ending "Nothing looks wrong — all metrics
nominal." Three stacked failures, the same class as the ADR-039 weather case (the second instance in
one day):
1. **No matching sensor.** The catalog had volume *actuators* (`volume_up/down`, `mute`, `unmute`)
   but no way to *read* the sound state. A model without the right perception primitive reaches for
   the nearest available action, and the output looks like an answer.
2. **The intent gate matched a noun, not an intent.** `_SYSTEM_QUERY` (v1.8.2) allowed `report_system`
   because the question contained the bare word "computer" — a word present in countless questions
   that aren't about system health.
3. **The report's verdict overflowed its scope.** "All metrics nominal" — said in reply to an audio
   question — implicitly asserted the audio was fine, based on metrics containing no audio information.

## Decision
Three rules, each enforced in code, applied to the audio instance now and binding on future actions:

1. **Sensor–actuator parity.** Every actuator family must have a matching read-only sensor: if JARVIS
   can *set* a state, it must be able to *read* it. Added `audio_status` (kind="diag", the parity
   sensor for the volume/mute actuators): one bounded `osascript get volume settings` through the
   sanctioned spawn seam → output/input/alert volume + mute, with deterministic interpretation flags
   ("output is MUTED — no sound will play…"). Pure parser (`parse_volume_settings`) tested offline.
2. **Intent gates match intent, not nouns — and every diag sensor gets one.** `_SYSTEM_QUERY` no
   longer contains bare device nouns (computer/machine/mac/laptop/system); device words count only
   inside explicit health phrasings ("how's my mac", "is my computer ok", "check my mac", "system
   report"). The new sensor gets its own symmetric gate (`_AUDIO_QUERY`: volume/sound/mute/speakers/
   hear/…) so `audio_status` can never become the 7B's next generic "let me check" escape hatch —
   the proposal/disposal split (prompt proposes, code disposes) now covers both sensors uniformly.
3. **Scope-honest verdicts.** A report's conclusion must name what it measured: `report_system` now
   ends "Nothing looks wrong **in these metrics (CPU / memory / disk / battery)**"; `audio_status`
   states it reads the software audio state only and points at the keyboard-settings/hardware
   possibilities it cannot see. A clean report can never again masquerade as a verdict on something
   it didn't measure. The prompt additionally teaches: if no status action matches the capability
   asked about, say plainly it can't be checked yet — never substitute a different report.

## Consequences
- **Gained:** "why isn't my volume working?" now gets the actual sound state with an actionable flag
  (muted / at zero / very low), or an honest "software state is fine; this doesn't test speakers or
  keys." The probe sentence is a permanent regression fixture.
- **Binding rule for future work:** adding an actuator without its read-back sensor is now an ADR-040
  violation flagged in review; new diag sensors must ship with their own intent gate.
- **Tightening risk accepted:** some loose health phrasings ("my mac feels weird") no longer trigger
  `report_system` via a bare noun; the explicit phrasings still do, and the model can still answer in
  words. Conservative direction: a missed report is a smaller failure than a wrong-question report.
- The four-instance gate dispatch in `_run_actions` stays per-name `if`s for two sensors; if a third
  sensor lands, generalize to a table (sensor → gate) rather than a third branch.

## Alternatives Considered
- **Fold audio into `report_system`** — rejected: conflates "is the machine healthy" with "what is
  this device's state"; the verdict-scope problem would get worse, not better.
- **Let the LLM decide when a report applies (no code gate)** — rejected: v1.8.2 already proved the
  7B uses diag actions as a generic escape hatch; prompt-only steering is not disposal.
- **A generic `device_status <thing>` action** — rejected: an open argument surface on a diag action
  invites free-form probing; the closed catalog stays closed — one named sensor per capability.
