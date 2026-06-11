# ADR-047: `largest_apps` sensor — and the decision to unify read-only inspection

## Status
Accepted. `largest_apps` ships now as a **stopgap**; the architectural decision it triggered — a single
unified read-only inspection tool — is **accepted in principle, deferred to post-review** (will be its
own ADR when built, likely ADR-048). Suite 503 → **506**.

## Context
Asked "what's the largest application installed?", JARVIS ran `find_file` (a *filename* search) with
the literal query "largest application" and returned nothing. Same missing-organ class as audio
(ADR-040) and network (ADR-046): no app/disk-size sensor existed, so the 7B grabbed its nearest tool.

Adding `largest_apps` would have been the fifth bespoke read-only sensor in a day. The user challenged
the pattern directly: *"for every single little thing we cannot build a sensor… why don't we have a
general sensor?"* That is correct, and it surfaced the real flaw in the closed-catalog model as applied
to inspection.

## Decision

### Immediate (this ADR): ship `largest_apps` as a stopgap
A read-only diag (`du -k -d 1 /Applications` through `safespawn`; pure `parse_du_sizes`; intent-gated
by `_APPS_QUERY`; scope-honest verdict — /Applications only). Live-verified. It resolves the immediate
question and is cheap to retire once the general tool lands.

### Architectural (accepted in principle, deferred): one unified read-only inspector
The closed catalog conflates **mutation risk** (writes/exec — `rm`, `kill`, `sudo`: genuinely
dangerous, must stay locked behind the catalog + consent ledger) with **information risk** (reads —
`du`, `ps`, `lsof`, `df`, `system_profiler`: idempotent, cannot harm the machine). Per-question
*read* sensors are unscalable sprawl; the safety that justifies the closed catalog applies to *writes*,
not reads.

Resolution: a single `inspect_system` capability, bounded so it can **read anything, mutate nothing**.
- **Phase 1 — deterministic allowlist (post-review):** `inspect_system(binary, args)` validates
  `binary` against a hardcoded read-only set (`du, df, ps, lsof, nettop, system_profiler, vm_stat,
  networksetup, …`) and rejects shell metacharacters / piping in args. Collapses the 5 bespoke sensors
  into 1 generic tool. Buildable with no kernel config.
- **Phase 2 — OS write-deny sandbox:** run the chosen command under `sandbox-exec` with
  `(deny file-write*)`, so purity is OS-enforced, not allowlist-enforced — fully general. This is the
  same sandbox the overnight-coder track (ADR-043 line) already requires; the tool definition the model
  sees is identical across phases.

The **write/actuation firewall is unchanged** — only the read path is generalized. Existing read
sensors (`report_system`, `audio_status`, `network_status`, `largest_apps`) become thin conveniences or
are retired once `inspect_system` lands.

## Consequences
- **Now:** the largest-app question works; suite +3.
- **Direction locked:** no more bespoke read sensors should be added — route new "inspect my Mac"
  questions to the planned `inspect_system`. This ADR is the record of that decision.
- **Sequencing:** Phase 1 in the post-June-16 window; Phase 2 with the OS sandbox. Tool definition
  stable across the swap, so the model/prompt don't change when the enforcement layer upgrades.

## Alternatives Considered
- **Keep adding per-question read sensors** — rejected by the user and on merit: unscalable, and each
  one is an ADR + gate + tests for a single question.
- **Give the 7B a general shell now** — rejected: that reopens mutation risk (the exact thing the
  closed catalog prevents). The point is read-only generality, enforced by allowlist then sandbox.
- **Sandbox-first (skip the allowlist)** — deferred, not chosen: `sandbox-exec` edge cases across
  macOS/hardware need vetting; the allowlist delivers the structural win immediately and the sandbox
  swaps in behind the same tool definition later.
