# ADR-048: Auto-start the Flow Sentinel at boot (persist on/off)

## Status
Accepted & live-verified. Suite 506 → **507**.

## Context
The user noticed JARVIS stopped learning app-activity routines after a restart. Investigation: the
**Flow Sentinel** (the app-focus observer that feeds habit context and the steadiness brain) only
started via an **explicit `sentinel on` command** — there was *no* auto-start anywhere in the daemon's
boot path. So after every daemon/machine restart it sat silently off until someone typed the command.
The user confirmed: *"we started the sensor manually yesterday… this does not make sense for us to turn
it on every single time."* The cost was invisible and real — habits formed that day lost their
foreground-app dimension (`scope=base`, empty `app`) because no foreground context was being observed,
and the steadiness brain learned nothing.

## Decision
The sentinel **auto-starts at daemon boot**, and the user's on/off choice **persists** across restarts:
- `SentinelStore` gains a `sentinel_settings` key/value table with `enabled()` / `set_enabled(on)`.
  `enabled()` **defaults to on** when never set — observing routines is the assistant's core job, so it
  should learn by default.
- `Session.__init__` calls `self._flow.cmd("on")` when `enabled()` — wrapped so any failure (no
  `swiftc`, headless CI) is swallowed and the daemon boots normally; the sensor just stays off.
- The `sentinel` dispatch handler persists the choice: `sentinel off` writes `enabled=0` (and survives
  restart — a deliberate off is respected), `sentinel on` writes `enabled=1`.
- The daemon's `select()` loop already gathers session-owned fds each iteration, so the sensor pipe is
  picked up automatically once the sentinel starts during init — no server change needed.

## Consequences
- **Gained:** start JARVIS → it observes and learns app routines, every time, with no manual step.
  Habits regain their foreground-app context automatically. Live-verified: a fresh daemon boot brought
  the sensor up with no command and it began observing immediately.
- **Respects intent:** a user who runs `sentinel off` stays off across restarts (persisted), so
  auto-start isn't a default that overrides a deliberate choice.
- **Safe:** auto-start failure never blocks boot; UI-actuation (app hiding) remains consent-gated and
  dry-run-able exactly as before — only the observation/learning path is what now starts by default.
- The overnight runner is intentionally **not** auto-started (it requires an explicit committed batch);
  this ADR changes only the always-on observer.

## Alternatives Considered
- **Auto-start always, no persistence** — rejected: it would override a user who deliberately turned
  the sentinel off, re-enabling it on every restart (the opposite annoyance).
- **Leave it manual, document it** — rejected: the failure is silent (no error, just no learning), so
  documentation wouldn't prevent the lost-data days; the default must be correct.
- **Auto-start via the menu-bar app instead of the daemon** — rejected: learning must not depend on the
  GUI being up; the daemon owns observation, so the daemon owns auto-start.
