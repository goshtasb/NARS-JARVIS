# ADR-046: Network-inspection sensor (the local "what's using my connection" organ)

## Status
Accepted & live-verified. Realizes the ADR-040 sensor-parity principle for the network. Suite
498 → **503**.

## Context
The user asked JARVIS, by voice, *"something is slowing down the internet, find out what it is."*
JARVIS ran a web search and returned generic advice ("router issues, network congestion…") — it never
looked at the actual machine. When the same question was investigated by hand (lsof / nettop /
system_profiler), the answer was specific and local (which apps held connections, who used bandwidth,
Wi-Fi link quality). The gap: **the closed catalog had no network sensor.** Per the recurring lesson
of this project, a model with no sensory organ for some data reaches for its nearest tool — here, web
research — and produces something that looks like an answer but isn't a measurement.

## Decision
Add `network_status` — a read-only diag action that inspects THIS Mac, following the `audio_status`
(ADR-040) pattern exactly:
- **Pure parsers** (`parse_nettop_delta`, `parse_connections`, `parse_wifi`) over the CLI output,
  unit-tested on fixtures with no network access.
- **`net_report(spawn)`** runs three hardcoded, argv-only primitives through the sanctioned
  `safespawn` seam (never a shell string — stricter than raw `subprocess.run`):
  - `nettop -P -d -L 2 -s 2 -J bytes_in,bytes_out` → **per-process bandwidth** (the interval delta:
    each process is emitted cumulative-then-delta, so the parser keeps the *last* value).
  - `lsof +c 0 -nP -iTCP -sTCP:ESTABLISHED` → **open connections per process** (peer located by the
    `->` token, robust to lsof's IPv4/IPv6 column differences; loopback skipped).
  - `system_profiler SPAirPortDataType` → **Wi-Fi link quality** (PHY/channel/signal/tx-rate).
- **Intent-gated** (`_NETWORK_QUERY` / `_is_network_query`): like report_system/audio_status, it runs
  only when the user's text is about the internet/network/Wi-Fi — the 7B can't grab it as a generic
  "let me check". The prompt also tells the model to use `network_status` (not `web_lookup`) for
  network questions, and only search the web afterward if the user asks for *tips*.
- **Scope-honest verdict** (ADR-040/045): the report names the biggest local consumer, states plainly
  it is/ isn't JARVIS, and that it sees only this Mac — not the router, ISP, or other devices.

**No network egress.** The sensor is local inspection only — it deliberately does **not** ping. This
preserves the project's "local-first, one declared egress (web search)" identity. Wi-Fi tx-rate at a
known signal already flags a weak link without leaving the machine. (A bounded latency/`ping` check —
the PM's `latency_check` — is a reasonable *opt-in* extension, but it is a second egress path and is
deferred to an explicit decision rather than added silently.)

**Surfacing:** explicit, intent-gated — **not** auto-run as a side effect of a web-research timeout.
Auto-running would fire network inspection (seconds of `nettop`) for an unrelated failure, exactly the
"system acts unrequested" anti-pattern fixed repeatedly elsewhere (ADR-044/045). The user asks; the
sensor answers.

## Consequences
- **Gained:** "what's slowing my internet?" now returns this Mac's real state — top bandwidth apps,
  busiest connections, Wi-Fi quality — and explicitly clears or implicates JARVIS. Live-verified:
  named mDNSResponder/Superhuman as the local consumers, JARVIS as not-it.
- **Honest limits (in the report itself):** it sees only this machine, not the router/ISP/other
  devices; and it has no latency number (no ping, by the egress decision above).
- **Note on the PM blueprint:** the proposed `parse_nettop_output` matched on `tcp`/`udp`/`0 B`
  markers that the `-J` CSV format does not contain — it would have returned an empty list on real
  output. The shipped parser is verified against live `nettop`.

## Alternatives Considered
- **`subprocess.run` with a `mode` argument** (PM blueprint) — adapted, not adopted verbatim: argv
  goes through `safespawn` (env-scrubbed, the project's only sanctioned spawn), and the action takes
  no free argument (one composite report), removing an arg-validation surface and giving the whole
  picture in one shot.
- **JSON output to the model** — rejected: every other sensor (report_system, audio_status) returns
  human-readable text that is *both* shown to the user and read by the model; the composite report is
  ~6 lines, well under the token budget, so JSON adds inconsistency for no gain.
- **Including a `ping` latency check** — deferred (see Decision): real diagnostic value, but a second
  egress path for a privacy-first local assistant; opt-in, documented, not silent.
- **Auto-running on web-research timeout** — rejected (see Decision): couples subsystems and acts
  unrequested.
