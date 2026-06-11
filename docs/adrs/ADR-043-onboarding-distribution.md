# ADR-043: One-command onboarding — prebuilt ONA + guided install

## Status
Accepted. Shipped with the first public release (v1.14.4 release assets). Scope was strictly
deployment engineering — zero runtime-code changes, so the field-test telemetry stays valid.
(Note: "ADR-043" was provisionally floated for the overnight coder in discussion; that work, still
gated on its feasibility numbers and an OS sandbox, will take the next free number.)

## Context
The repo went public; a project that is hard to install does not get adopted. The hard parts of our
install were: compiling the ONA C reasoner (needs clang and trust in a build script), Python
dependency hygiene, multi-GB model downloads, and the macOS TCC permission grants no installer can
automate. The PM blueprint proposed a Homebrew tap with prebuilt bottles plus a guided TCC stage.

## Decision
1. **Prebuilt ONA binary as a GitHub release asset** (`ona-macos-arm64.tar.gz` on v1.14.4): the exact
   binary this daemon runs daily — i.e. *tested by production use* — packaged with the upstream MIT
   license and a PROVENANCE.txt (upstream commit, build date, how to build from source instead). The
   installer verifies it by **pinned SHA256** before unpacking; a mismatch refuses to install.
2. **`install.sh` (curl-able, idempotent)** with a deterministic automated stage — platform gates,
   Xcode CLT check, clone-or-update, isolated `.venv` (system Python untouched; launchers prefer the
   venv when present), ONA fetch+verify — and a **consent-per-download model stage**: each multi-GB
   artifact (chat 7B, embedder, voice, Chromium) is offered individually and skippable, with the
   degradation named honestly ("without it, X is off"). Nothing large downloads silently.
3. **The guided TCC stage stays manual by design:** the script prints what's needed, deep-links the
   right System Settings pane, and never attempts to automate a permission grant — automating TCC
   would be an OS-level vulnerability, not a convenience.
4. **Apple Silicon only, stated plainly.** The assistant's brain is a Metal-resident 7B; on Intel it
   degrades to CPU inference at unusable speeds. The installer refuses on x86_64 with an honest
   message instead of shipping a bad experience.
5. **Homebrew tap deferred (not rejected).** A formula needs stable asset URLs + checksums (which now
   exist) but also brew-idiomatic Python handling and a separate `homebrew-jarvis` tap repo;
   `install.sh` already delivers the one-command goal. The tap is the brew-native polish to add once
   the installer has survived first-user contact.

## Consequences
- **Gained:** `curl | sh` → working menu-bar assistant, no C toolchain, no dependency pollution,
  binary integrity checked, every large download consented.
- **Paid:** the release asset must be rebuilt/re-pinned when ONA upstream is bumped (the SHA256 in
  `install.sh` is the forcing function — an update without re-pinning fails closed).
- **Risk accepted:** `curl | sh` is a trust decision users make about the repo; mitigations are the
  pinned checksum for the only binary artifact, a readable script, and per-download consent.
- **Untested path:** first-run on a *fresh* Mac (no CLT, no Python history) is designed for but not
  yet verified on virgin hardware — first-user reports will be the test. Stated rather than assumed.

## Alternatives Considered
- **Homebrew formula first** — rejected as the opening move: a formula references release assets, so
  assets must exist first; and `install.sh` reaches users without requiring brew at all.
- **Compile ONA on the user's machine** — rejected for onboarding (clang/CLT friction is exactly the
  drop-off point); still fully supported as the manual path and documented in PROVENANCE.txt.
- **Intel (x86_64) support via cross-compiled binaries** — rejected honestly: the binary would build
  but the assistant experience on CPU-only inference is unusable; shipping it would invite the exact
  ridicule a polished public repo is meant to avoid.
- **Auto-downloading models without asking** — rejected: multi-GB pulls on first run without consent
  is hostile; the per-artifact prompt with named degradation is the respectful default.
