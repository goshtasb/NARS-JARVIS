# ADR-022: Self-navigating action recipes (+ the stable-signing root-cause fix)

## Status
Accepted. Makes "set brightness to X" work from any focused app, with no pre-opened settings pane and
no approval click. Also records the **root-cause fix** that finally made AX actuation reliable:
stable code-signing. Python suite 345 → **348** green.

## Context
ADR-021 gave JARVIS GUI actuation, but two things made it unusable in practice:

1. **It only acted on the focused window.** "Set brightness" failed unless the user had *already*
   opened System Settings → Displays — which defeats the purpose of an assistant. (Live: JARVIS saw
   "50 controls from Google Chrome," no Brightness slider, and correctly declined.)
2. **The Accessibility grant died on every rebuild.** The app was ad-hoc signed, so its Designated
   Requirement was the cdhash — which changes each build. Every fix I shipped required a rebuild,
   which silently revoked the grant and blinded JARVIS. Re-granting was a symptom-patch the next
   rebuild always undid.

## Decision

### Root-cause fix — stable code-signing (the real cure for #2)
Sign the app with a persistent **self-signed identity** ("JARVIS Self-Signed") so its Designated
Requirement is **identity-based and constant across rebuilds**:
```
designated => identifier "com.nars.jarvis" and certificate leaf = H"f36c674d…"   ← fixed forever
```
TCC binds the Accessibility grant to that DR, so it **survives every future rebuild**. Grant once,
done. `ui/setup-signing.sh` creates the identity (idempotent; key+cert imported as separate PEMs
because macOS `security import` rejects a modern OpenSSL PKCS#12 MAC; the cert is an untrusted x509
root, which is fine — `codesign` signs with it and TCC only needs the DR to match). `ui/build.sh`
signs with it when present, ad-hoc fallback otherwise.

### Self-navigation recipes (the fix for #1)
A new action **kind `"nav"`**: high-level verbs where the daemon opens the right surface itself, waits
for it, and actuates — so they work regardless of what's focused.
- `set_brightness <0-100>` — curated and **safe/reversible, so NO consent gate** (unlike the general
  `ax_*` verbs). Always listed in the prompt (DOM-independent), so the model emits it from anywhere.
- Flow (`session._nav_dispatch`): if a Brightness slider is already on screen, set it now; else `open`
  the Displays deep link (`x-apple.systempreferences:com.apple.Displays-Settings.extension` via
  `safespawn`) and stash a pending request. `_ax_context` fulfills it when the pane's controls arrive
  (`find_control_id` locates the `AXSlider "Brightness"`); `tick()` expires it after 8 s. The
  actuation reuses ADR-021's `actuate` event → the app sets the slider. Daemon-only — no app rebuild.

## Consequences
- **Gained:** "set brightness to 45%" works from any app, hands-free (JARVIS opens Displays, sets it,
  no approval). The Accessibility grant is now permanent across rebuilds — the recurring failure is
  cured at the root, not patched.
- **Tests:** +3 (`set_brightness` catalog entry; `find_control_id`; converse nav routing). All
  pure/stubbed — no OS side effects. Live-verified end-to-end on the M3 Pro XDR panel.
- **Honest limits / deferred:**
  - **Recipe, not general navigation.** Only `set_brightness` is wired. The general "open → re-perceive
    → act" agent loop for arbitrary targets is the next step; this is the reliable first instance.
  - **Pane-render race:** if the Displays pane renders slower than JARVIS reads it, the first try can
    miss the slider (8 s timeout → "couldn't find"); a second try is instant. A post-activation
    re-serialize in the app would make it reliably one-shot (deferred; now cheap since rebuilds keep
    the grant).
  - **macOS gotcha:** a freshly-granted permission needs the app **relaunched** to take effect (the
    grant itself is correct).
  - **Self-signed identity is per-machine:** `setup-signing.sh` recreates it on a new machine; the
    private key lives only in the login keychain (never committed).

## Alternatives Considered
- **Recreate the grant after each rebuild (patch):** rejected — it never held; rebuild churn was the
  disease, stable signing is the cure.
- **`brightness` CLI / DisplayServices:** rejected — fails on this Apple-Silicon XDR panel
  (`-536870201`); AX on the real slider is what works.
- **Consent-gate `set_brightness` like the raw `ax_*` verbs:** rejected — it's a curated, safe,
  reversible setting; gating it added friction for no safety gain. General GUI verbs stay gated.
