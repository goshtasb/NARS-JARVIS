# ADR-017: Operational resilience — app auto-reconnect, liveness launcher, restart helper

## Status
Accepted. Stabilization prerequisite for the live Flow-Sentinel dry-run (the run that derailed when
the menu-bar app died after a daemon churn).

## Context
During the first live dry-run the menu-bar app went dead after I restarted the daemon under it.
Code review pinned the causes (no Python involved — this is a UI/launcher concern):
- **`JarvisClient` never reconnected.** `readLoop` exited silently on a dropped socket; pending calls
  hung, no events arrived, writes to the dead fd were ignored — the app **zombied** (looked
  "crashed"). There was **no `.ips` crash report and no jetsam memory-kill** — confirming a
  silently-dead IPC, not a hard crash.
- **`run-ui.sh` checked socket *file existence*, not *liveness***, so a stale socket file (left by a
  non-clean kill) made it skip starting the daemon and connect the app to nothing; it also always
  re-`open`ed the app. Compounded by my wrong `pkill` pattern, this left two daemons + a dead app.

## Decision
- **`JarvisClient`**: a one-shot `onDisconnect` callback fires (once) when `readLoop` sees the socket
  drop; `close()` suppresses it for intentional teardown.
- **`AppDelegate`**: connection is now a background **retry-with-backoff** loop (0.5s→cap 5s) that
  also runs on `onDisconnect`, so the app (a) waits for the daemon if launched first and
  (b) **auto-reconnects across any daemon restart** — rewiring `client` *and* `chat.client` and
  reflecting state in the menu-bar title (`🔵` connected / `⚪` reconnecting).
- **`run-ui.sh`**: a real **liveness probe** (attempt a socket connect) replaces the file check —
  clears a stale socket and starts fresh when nothing is listening, reuses a live daemon otherwise;
  **rebuilds the app if any `*.swift` is newer** than the binary; and **skips `open` if the app is
  already running** (the app reconnects to the fresh daemon).
- **`ui/restart.sh`** (NEW): one foolproof command — quiesce daemon + sensor + app with the *correct*
  patterns, clear the socket, then `exec run-ui.sh` (env flags pass through). The footgun I hit is
  now structurally unhittable.

## Consequences
- **Gained:** the app survives daemon restarts/redeploys instead of zombieing; launch ordering no
  longer matters; the two-daemon/stale-socket footgun is gone; `restart.sh` is the canonical reset.
- **Verified:** `pytest` unchanged (**272**, no Python touched); `build.sh` compiles; live cycle —
  app **survives** a daemon kill, and after relaunch there is exactly **1 daemon + 1 app** (no dups).
- **Honest limit:** this layer is **shell + Swift, with no pytest harness** — verification is the live
  checklist. The *headless* checks confirm the app process survives a daemon restart and the launcher
  de-dupes; the **GUI-level confirmation** (menu-bar `⚪→🔵` and the chat usable again after a restart)
  is the user's to eyeball, since there's no headless signal of the app's internal reconnect.
- **Scope:** operational/launch only — no change to the brain, memory, grounding, sentinel math, or
  execution security. Arming real actuation remains a separate, later, human decision.
