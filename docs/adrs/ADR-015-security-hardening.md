# ADR-015: Security architecture & execution hardening

## Status
Accepted (Phase A — near-term hardening). Phase B (App-Sandbox helper) is scoped but deferred.

## Context
A targeted architectural security review (no automated scan) of the actuation surface found the
execution path is already minimal and adversarially tested (closed typed catalog, only `df -h` live,
deny-default `sandbox-exec` subset, 0 escapes in the 2026-06-05 crucible). The residual risks are
**structural**, not RCE:
1. **Env-filter separation** — secret isolation depended on a per-call Python `_filtered_env`; a future
   raw `subprocess` spawn would inherit `os.environ` (which carries ambient shell secrets) and leak.
2. **Un-sandboxed UI actuator** — `Sensor.hide/unhide` (→ Swift `NSRunningApplication.hide`) is a
   command source outside the sandbox; a context-manipulated trigger could spam visibility (DoS).
3. **Coarse network** — `sandbox-exec` egress is all-or-nothing; a future network-granted autonomous
   op would exfiltrate freely.

## Decision (Phase A — implemented)
**Anchor 1 — secrets cannot leak via any spawn.** New `src/safespawn.py` is the single sanctioned
seam:
- `scrub_environ()` **pops every secret-marked var out of `os.environ` in place**, called at the very
  top of `server.main()` and `console.main()` before anything spawns. The process environment then
  holds no secrets, so `subprocess`'s inherit-`os.environ` default is **safe by construction** — the
  root fix, not a per-site convention. (Safe because the app needs no cloud key at runtime: local
  GGUF / whisper / `say` / `df` / ONA — the env secrets are purely ambient.)
- `run()`/`popen()` wrappers **reject `shell=True` and non-list argv** and refuse to pass a
  secret-bearing env. **All six spawn sites** (`brain/ona`, `service/voice` whisper+`say`,
  `sentinel/sensor` swiftc+helper, `execution/sandbox_client`, `console` daemon-spawn) route through
  them; `_SECRET_MARKERS` is unified here.
- `test_no_raw_subprocess.py` — an **AST scan** that fails the suite if any module calls
  `subprocess.{Popen,run,call}` directly (outside `safespawn`). Structural enforcement at CI time
  (Python has no runtime access control). The wrapper can no longer be bypassed silently.

**Anchor 3 — actuator rate limit.** `Sensor` now token-buckets `hide/unhide` (reusing
`sentinel.limiter`; `rate=0.5/s, capacity=4`); excess toggles are dropped + counted (`_actuate_overflow`,
logged). It's the single command source for all hides, so it bounds every trigger regardless of cause.

**Anchor 2 — network invariant frozen.** `execution/test_network_invariants.py` asserts every live
operation is air-gapped, `requires_network` is default-deny, no catalog template carries a network
token, the executor refuses network ops for live, and `air_gapped.sb` allows no network. A future op
that breaks "no autonomous network" fails the build.

## Decision (Phase B — deferred, documented)
Replace deprecated `sandbox-exec` with a **signed macOS App-Sandbox helper** (kernel-enforced clean
environment + jailed read ops). **Micro-VM rejected** — the actions are host-actuation (read host disk,
open/hide host apps), which a guest VM cannot do without dissolving its own isolation; VMs fit
untrusted *compute*, which this architecture deliberately lacks. Honest limits carried forward: App
Sandbox is **also** coarse on network (so network, if ever needed, goes through a separate
human-mediated, domain-allowlisted **egress broker**, never a raw grant), and cross-app actuators
(open/hide) can't be meaningfully jailed by any sandbox — they stay bounded by **policy** (the NARS
autonomy gate + this rate limiter + the undo path).

## Consequences
- **Gained:** secret exfiltration via subprocess is structurally impossible (no secrets in env + no
  bypassable spawn); actuator DoS is capped; the no-network invariant is test-locked.
- **No behavior change** for normal use; no schema/data change. Full suite 266 passed (+16).
- **Standing risks (tracked):** `sandbox-exec` is Apple-deprecated; coarse network remains a platform
  limitation handled by keeping network out of the sandbox entirely; App-Sandbox migration is future work.

## Alternatives Considered
- **Monkeypatch `subprocess.Popen` to auto-scrub:** rejected — fragile across third-party spawns,
  silently alters stdlib; `scrub_environ` + the AST guard achieve the same safety without the magic.
- **Per-call-site env filtering (status quo):** rejected — relies on every future dev remembering it.
- **Micro-VM execution tier:** rejected — host-actuation mismatch + huge overhead (see Phase B).
