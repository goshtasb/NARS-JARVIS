# Contributing to NARS-JARVIS

Thanks for your interest — a project like this getting unprompted attention means a lot.

**Set expectations first, honestly:** NARS-JARVIS is an **active, early-stage research/personal
project**. It moves fast, the architecture is still evolving (see the open work in
[`docs/adrs/`](docs/adrs/)), and some surfaces churn between commits. The *draw* here is the
architecture — a local-first, privacy-first neuro-symbolic assistant with a strong documentation and
decision trail — not a finished product. If that excites you, welcome.

## Before you write code: read the standards

These are **binding**, not suggestions:

1. **[`standards/00-manifest.md`](standards/00-manifest.md)** — routes you to the relevant
   sub-standards (modular decomposition, SOLID + functional-core/imperative-shell, file-size guidance,
   documentation). *Do not fabricate rules — if one isn't defined, ask.*
2. **[`CLAUDE.md`](CLAUDE.md)** — the project's ground-rule principles (continuous modular docs,
   modular architecture by default, no god files).
3. **The README's [Build on top of it](README.md#build-on-top-of-it-contributor-guide)** — the common
   extension points (a new action, a work primitive, a daemon command + UI surface).

## The non-negotiable invariants

Respect these or the PR won't land — they're the whole point of the project:

- **Model proposes, code disposes.** The LLM emits `[[DO:]]` directives; a *closed, validated catalog*
  decides what runs. There is no generative execution path. Never add one.
- **Content-blind by default.** Sensors read coarse signals (app *category*, system metrics) — **never
  window titles, URLs, document contents, or keystrokes.** That line keeps us out of invasive macOS
  permission prompts and is core to the privacy promise.
- **Local-first.** One declared network egress (read-only web research). Don't add telemetry, analytics,
  or new outbound traffic without an ADR that documents it honestly.
- **Consent-gated mutation.** Reversible actions can run; anything destructive or stateful goes through
  the consent gate. The "earns autonomy slowly, loses it fast" math lives in NARS, not the LLM.
- **Tell the truth.** No silent truncation, no fabricated results — surface coverage and failures.

## Workflow

1. **Open an issue / discussion first** for anything non-trivial, so we can agree on the approach before
   you invest time (the codebase moves fast — coordinating up front avoids wasted work).
2. **Branch** off `main`.
3. **One ADR per feature.** Significant changes get a short Architecture Decision Record in
   [`docs/adrs/`](docs/adrs/) — the decision, the rejected alternatives, and the honest limits. See the
   existing ADRs for the format.
4. **Document alongside the code** (Principle 1) — update the module's `README.md` in the *same* change.
5. **Tests are the contract.** Add tests next to the code you touch; the pure functional cores are
   unit-tested, and safety boundaries (consent, the read-only classifier, no-network) are explicitly
   asserted.
6. **Open a PR** with a clear description of what and why.

## Setup & running the tests

> **Platform:** macOS on Apple Silicon. You'll need Xcode Command Line Tools, Python 3, and a local
> GGUF chat model (the one-command `install.sh` handles the rest — see the README Getting Started).

```sh
# build the ONA reasoner (upstream, not vendored)
git clone https://github.com/opennars/OpenNARS-for-Applications && (cd OpenNARS-for-Applications && sh build.sh)
# python deps
pip install -r requirements.txt
# run the suite (from src/)
cd src && python3 -m pytest .
```

The suite is fully offline — it uses fakes for the model, so you don't need GGUF weights to run tests.

## Good places to start

We're not posting formal "good first issue" tickets yet (the code is still maturing), but genuinely
useful, well-scoped contributions right now include:

- **Test the one-command install on a fresh Mac** and report what breaks — `install.sh` is designed but
  not yet verified on virgin hardware, and that's the single most valuable thing a new contributor can do.
- **Expand the friendly app-name map** in `src/sentinel/usage.py` (the passive-observation mirror).
- **Documentation** — clarity fixes, module READMEs, examples.
- **Platform exploration** — Linux/Windows are out of scope today; a serious proposal is welcome.

If you're unsure whether something fits, **open a discussion and ask.** We'd rather talk first than have
you build the wrong thing.

## Conduct

Be respectful and assume good faith. See [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

By contributing, you agree your contributions are licensed under the project's [MIT License](LICENSE).
