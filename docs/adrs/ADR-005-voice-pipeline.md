# ADR-005: Push-to-talk voice — control/data plane split, whisper as a selected subprocess

## Status
Accepted

## Context
Phase 3 gives JARVIS ears and a voice while preserving every prior invariant: local-first/offline,
the thin Swift client (zero ML), the single-threaded lock-free daemon, and minimal macOS TCC
exposure. Voice introduces two hazards: (1) large PCM audio that must not clog the JSON IPC socket or
block the reasoning loop, and (2) a global hotkey, which the naive API (`NSEvent` global monitor)
turns into an Accessibility / Input-Monitoring permission dialog.

## Decision
**Ownership.** Swift owns the user-facing input devices — the **microphone** (`AVAudioRecorder`) and
the **hotkey** — because the `.app` bundle gives a clean, attributed Microphone TCC via
`NSMicrophoneUsageDescription`. Python (the daemon) owns the **ML and output** — **whisper.cpp STT**
and **`say` TTS** — keeping the Swift client free of any model (the ratified thin-client rule).

**Control plane / data plane split.** The audio bytes never cross the JSON socket. Swift records a
push-to-talk utterance to a 16 kHz WAV in `$TMPDIR` and sends only a tiny JSON pointer
(`{"cmd":"voice","arg":{"path":...}}`). The daemon reads the path, not the bytes.

**Whisper as a select()-multiplexed subprocess.** The daemon spawns `whisper-cli` (Popen, returns
instantly) and registers its stdout fd in the same `select()` loop that already watches the sensor
pipe. STT runs in its own process; the loop keeps serving clients/sentinel and never blocks on
inference. At EOF the transcript is routed through the normal command pipeline, and the reply is
spoken via `say` (fire-and-forget Popen). No worker threads, no locks — ONA stays single-owner.

**Hotkey via Carbon `RegisterEventHotKey`.** A *registered* hotkey (⌥Space) is not keystroke
monitoring, so it does not trigger the Accessibility/Input-Monitoring dialog. Carbon delivers pressed
and released, enabling true hold-to-talk. The **only** accepted TCC for voice is the Microphone.

**Runaway failsafe.** If the Carbon release event is swallowed (cmd-tab, system interrupt), a 30 s
Swift timer force-stops the recording, flushes the WAV, and dispatches — no runaway mic.

## Consequences
- **Easier:** no PCM in the JSON parser; STT can't stall reasoning; the hotkey needs no scary
  permission; the brain owns all models (Swift stays a thin client); fully offline after setup.
- **Harder / accepted:** one Microphone permission dialog (unavoidable for any voice feature); a new
  local dependency (whisper.cpp + a ~142 MB model, installed once via `ui/setup-whisper.sh`); Carbon
  is legacy (but compiles and runs under swiftc 6.3 / macOS 26 — verified).
- **Push-to-talk only:** a temp file fits bounded utterances. Continuous/live dictation would justify
  a dedicated binary streaming socket — the documented upgrade path, not built now.

## Alternatives Considered
- **Swift runs whisper:** rejected — bloats the thin client with ML, violating the ratified boundary.
- **Stream PCM over the JSON socket (base64 / binary):** rejected — chokes the line/JSON parser,
  bloats memory, and blocks the loop. The file-pointer split avoids it entirely.
- **`NSEvent.addGlobalMonitorForEvents` for the hotkey:** rejected — triggers the Accessibility/
  Input-Monitoring TCC dialog the project has avoided throughout.
- **Whisper on a worker thread + self-pipe:** rejected as unnecessary — a child process whose stdout
  is selected achieves non-blocking STT with no threads, matching the existing sensor pattern.
- **TTS in Swift (`AVSpeechSynthesizer`):** viable, but `say` in the daemon avoids a round-trip and
  keeps output next to the reasoning that produced it.
