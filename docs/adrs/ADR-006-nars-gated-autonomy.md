# ADR-006: NARS-gated autonomy — earning a state-changing action through learned consent

## Status
Accepted

## Context
The final capability: let the Sentinel act on its own for state-changing actions (e.g. hiding
distraction apps) instead of always prompting — but only when it has genuinely learned that the
action is wanted, and never in a way that undoes the security crucible. The naive idea ("autonomous
when ONA confidence ≥ 0.85") has a fatal flaw: in NAL, **confidence measures the amount of evidence,
not its polarity** — six *rejections* also drive confidence to 0.857. Gating on confidence alone
would autonomously execute an action the user has repeatedly refused.

## Decision
**The belief.** Per distraction *category* (comms/media — never per bundle id, so the macOS
`LSApplicationCategoryType` ontology keeps generalizing), the Sentinel brain holds a procedural
belief `<distracted_hide_<category> --> [approved]>`. This is in the Sentinel brain (behavioral),
keeping the Knowledge brain clean. ONA is the **evidence accumulator only** — `motorbabbling=0`; our
deterministic code still owns execution. *Math gates the trigger; code limits the blast radius.*

**The learning loop.** Every explicit `[y/n]` is fed back as NAL evidence with **asymmetric weights**:
- **Yes** = single-evidence `{1.0 0.5}` → confidence climbs the burn-in curve; ~6 approvals to earn.
- **No** = heavy-evidence `{0.0 0.9}` → collapses expectation in **one** decline.

Measured on real ONA: 6 yes → freq 1.0, conf 0.857, exp 0.929 (gate opens); 1 no → freq 0.40,
exp 0.41 (gate shut). Trust earned slowly, lost fast — the safety ratchet.

**The two-condition gate.** Autonomy iff **confidence ≥ 0.85 AND expectation ≥ 0.85**
(`expectation = conf·(freq−0.5)+0.5`), so it demands enough evidence *and* favorable polarity. This
closes the six-rejections hole.

**Autonomous ≠ invisible.** When the gate is clear the Sentinel acts immediately but emits an
`acted` event → a native notification with **Undo / Keep**. Undo un-hides the apps *and* feeds the
heavy negative evidence (revoking autonomy). So even autonomous actions remain correctable, and the
ratchet still applies.

**Tiered trust.** The catalog now classifies ops (`is_state_changing`, default-deny): read-only
inspections (`disk_usage`) skip the learning loop; state-changing actions must earn autonomy;
network-requiring ops remain *permanently* `[y/n]` (`requires_network`, from the crucible).

## Consequences
- **Easier:** the system genuinely "learns your habits" (consent accumulates into autonomy) using the
  *same* NAL evidence math as the burn-in; one click executes, teaches, and feeds the KPI.
- **Safer:** confidently-rejected actions can never fire; one decline revokes; autonomous actions are
  announced and undoable; the closed catalog and network rule are untouched. And there is now a hard
  kill switch (the `shutdown` command / Emergency Stop) — a prerequisite for shipping any autonomy.
- **Accepted:** category-level context means consent for "hide comms while distracted" authorizes it
  broadly across comms apps (the intended generalization, not per-app micro-consent). Autonomous
  KPI accounting double-counts an undone action slightly (recorded accepted at act time, declined on
  undo) — acceptable for a proxy metric.

## Alternatives Considered
- **Gate on confidence alone:** rejected — executes repeatedly-rejected actions (the core flaw).
- **Per-bundle context:** rejected — abandons the category ontology; every new comms app re-learns
  from scratch. The habit is "mute communications when distracted," not a specific bundle id.
- **Symmetric evidence:** rejected — trust must be lost faster than earned; declines carry more weight.
- **Silent autonomous action with no undo:** rejected — opaque and unsafe; `acted`+Undo keeps the
  human in control and preserves the ratchet.
- **Route hide through the M3 shell-command executor:** rejected — the hide is actuated by the Swift
  helper (`NSRunningApplication`), a different mechanism; forcing it into the air-gapped shell
  executor mismatches the model. The catalog gains the read/state-changing tier; the hide stays on
  its helper path under the Sentinel autonomy gate.
