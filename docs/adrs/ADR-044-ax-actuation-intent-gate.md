# ADR-044: Gate GUI actuation on UI-action intent (the phantom-click fix)

## Status
Accepted & live-verified. Extends ADR-021 (GUI actuation) with the same proposal/disposal discipline
as ADR-040/042. Suite 494 → **496**.

## Context
User report with a screenshot: on an ordinary conversational turn ("So is your name actually Jarvis
or is it something else I need to call you?") JARVIS answered correctly **and then popped an approval
prompt — "Approve: click button_23"**; approving it clicked a random on-screen control ("X").

Root cause: ADR-021 injects the focused window's accessibility controls into **every** converse
prompt, prefixed "On-screen controls (you may act on these)" with instructions for emitting
`[[DO: ax_press: <id>]]`. A small quantized 7B, shown a list of clickable controls and invited to act,
appends a spurious actuation directive after answering an unrelated question — and the consent gate
faithfully queues it. This is the exact failure class seen all day (report_system on a sunrise
question, web_search for facts): the model proposes an action nobody asked for, and here the prompt
actively provokes it. Worse than the others, because the proposed action *mutates the GUI*.

## Decision
A deterministic UI-action-intent gate (`_UI_ACTION_INTENT` / `_is_ui_action_request`), applied on
**both** sides — the established "prompt proposes, code disposes" split:

1. **Injection gate (remove the provocation + save prefill):** the focused-window controls block is
   added to the converse prompt **only when the user's text shows UI-action intent**. A plain chat
   turn never sees "you may act on these", so it is never tempted — and every non-action turn drops
   ~hundreds of prompt tokens (a small latency win on top of ADR-044's point).
2. **Disposal gate (the firewall):** in `_run_actions`, any `kind=="ax"` directive is **dropped**
   when the user's text shows no UI-action intent — even if the controls weren't injected, the model
   could echo an id from a prior turn. Code is the last word; a phantom click cannot reach the consent
   gate.

The gate matches **strong, rarely-conversational tokens** (click/press/tap/toggle/checkbox/slider/
button/drag/…) plus two narrow patterns ("set X to …", "select/choose/enable/disable the …").
Conservative by design: a missed soft phrasing ("select the option") is a smaller failure than a
phantom click — the user rephrases. Verified: the report's exact sentence and the day's other probes
gate to *no actuation*; genuine requests ("click the submit button", "set brightness to 45%") pass.

The bounded **agent loop** (ADR-024, `_drive_agent`/`agent_step`) is a separate path the user has
already opted into by issuing an action goal; it is **not** gated here.

## Consequences
- **Gained:** chat turns can no longer produce a phantom approval prompt or a random click; the GUI
  actuation surface only arms when the user actually asked to operate a control. Non-action turns also
  prefill less.
- **Paid / known tradeoff:** soft actuation phrasings outside the token set don't actuate (rephrase);
  a pure *state-reading* question that used to lean on the injected DOM ("how bright is my screen?")
  no longer sees it — which is arguably correct: per ADR-040 parity that should be a brightness
  **sensor**, noted as a follow-up, not patched here.
- **Risk accepted:** the word "button" appears in the audio "volume button" question — but that routes
  to `audio_status` (ADR-040) and never to actuation, so no phantom click results.

## Alternatives Considered
- **Prompt-only ("only click when explicitly asked")** — rejected: the day's evidence (and the
  v1.8.2 precedent) is that 7B prompt adherence cannot be the firewall.
- **Disposal gate only, keep injecting always** — rejected as insufficient *and* wasteful: it would
  stop the click but keep provoking the model and keep paying the prefill every turn. Gating injection
  too removes the temptation at the source.
- **Per-id confirmation wording change** — rejected: the consent gate worked correctly; the bug was
  upstream (an action proposed that never should have been).
