#!/usr/bin/env python3
"""Zero-config demo of NARS-JARVIS's deterministic grounding gate.

The pitch in one line: **the LLM proposes a structured claim; the SOURCE TEXT — not the model — grades it.**
This script runs the REAL production gate (`src/language/verify_gate.py`, pure stdlib: `re` + dataclasses)
on a handful of contract clauses. No local model, no download, no API key, no daemon — it imports the
exact code that guards what reaches the NARS memory vault and shows its verdict on each proposed claim.

  Default (zero-config):  python3 examples/grounding_gate_demo.py
      Feeds canned, REPRESENTATIVE LLM proposals (the failure modes the project's extraction harness
      surfaced) through the real gate. Deterministic: identical verdicts on every machine, every run.

  Live (skeptic mode):    python3 examples/grounding_gate_demo.py --live [--provider openai|anthropic]
      Prompts a real cloud model with the PRODUCTION extractor prompt (language.guarded_extract.
      GUARDED_PROMPT) over the same source clauses, then runs its live output through the same gate.
      Needs OPENAI_API_KEY (or ANTHROPIC_API_KEY). NOTE: the local build constrains the model with a
      GBNF grammar; over the network we can only PROMPT for JSON and parse leniently, so a live model
      may occasionally emit unparseable output — that is a property of the model, not the gate.

What this demo does NOT claim: it showcases ONE component in isolation — the deterministic write-gate.
It does not run ONA's reasoning, the two-brain runtime, or the end-to-end system.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from language.verify_gate import GateResult, verify   # noqa: E402 — after sys.path wiring

# ── ANSI colour, but only when writing to a real terminal ──
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


BOLD = lambda s: _c("1", s)        # noqa: E731
DIM = lambda s: _c("2", s)         # noqa: E731
GREEN = lambda s: _c("1;32", s)    # noqa: E731
RED = lambda s: _c("1;31", s)      # noqa: E731
YELLOW = lambda s: _c("1;33", s)   # noqa: E731
CYAN = lambda s: _c("36", s)       # noqa: E731


# ── the source clauses (verbatim, the only "ground truth" the gate consults) ──
BREACH = "Vendor shall notify Customer in writing within seventy-two (72) hours of discovering any data breach."
LIAB = ("In no event shall either party's aggregate liability exceed the total fees paid by Customer in "
        "the twelve (12) months preceding the claim.")
PAY = ("Buyer shall pay all undisputed invoices within thirty (30) days of receipt; overdue amounts shall "
       "accrue interest at 1.5% per month.")
FORCE = "Neither party shall be liable for any failure to perform due to causes beyond its reasonable control."


# ── canned, REPRESENTATIVE LLM proposals (each is the kind of output a real extractor emits) ──
# Every entry: a human title, the source clause, and the structured claim the LLM "proposed".
CANNED = [
    ("Faithful deadline — should be ADMITTED", BREACH, {
        "type": "TemporalClaim", "deontic": "shall", "subject": "Vendor", "action": "notify",
        "object": "Customer", "within_value": "72", "within_unit": "hours",
        "evidence": "Vendor shall notify Customer in writing within seventy-two (72) hours"}),

    ("Fabricated number: 1.5%  ->  '1500 basis points'", PAY, {
        "type": "RelationClaim", "deontic": "shall", "subject": "overdue amounts",
        "verb": "accrue interest", "object": "1500 basis points per month",
        "evidence": "overdue amounts shall accrue interest at 1.5% per month"}),

    ("Mis-binding: a 12-month look-back WINDOW read as a 12-month DEADLINE", LIAB, {
        "type": "TemporalClaim", "deontic": "shall", "subject": "either party", "action": "be liable",
        "object": "aggregate liability", "within_value": "12", "within_unit": "months",
        "evidence": ("either party's aggregate liability exceed the total fees paid by Customer in the "
                     "twelve (12) months preceding the claim")}),

    ("Fabricated citation: invents a 'Regulator' obligation not in the text", BREACH, {
        "type": "RelationClaim", "deontic": "shall", "subject": "Vendor", "verb": "notify",
        "object": "Regulator", "evidence": "Vendor shall notify the Regulator immediately"}),

    ("Dropped negation: 'Neither party shall be liable'  ->  'shall be liable'", FORCE, {
        "type": "ConditionalClaim", "deontic": "shall", "if": "Neither party",
        "then": "shall be liable for any failure to perform",
        "evidence": "Neither party shall be liable for any failure to perform"}),
]


def _verdict_line(r: GateResult) -> str:
    if not r.admit:
        return RED("REJECTED") + DIM("  (fail-closed — never written to memory)")
    if r.degraded:
        return YELLOW("DEGRADED") + DIM("  (unverifiable slot stripped; verified core kept)")
    return GREEN("ADMITTED") + DIM("  (fully grounded in the source)")


def _show(title: str, source: str, claim: dict, r: GateResult) -> None:
    print(BOLD("• " + title))
    print("    " + DIM("source clause : ") + CYAN(source))
    asserted = claim.get("object") or claim.get("then") or claim.get("value") or claim.get("verb") or ""
    print("    " + DIM("LLM proposed  : ") + f"{claim.get('type')}  ->  {asserted!r}")
    print("    " + DIM("gate verdict  : ") + _verdict_line(r))
    print("    " + DIM("reasons       : ") + ", ".join(r.reasons))
    if r.kept is not None and (r.degraded or not r.admit):
        print("    " + DIM("kept          : ") + json.dumps(r.kept))
    print()


def run_canned() -> int:
    print(BOLD("\nNARS-JARVIS — deterministic grounding gate  ") + DIM("(real verify_gate.py, no model)\n"))
    print(DIM("The LLM proposes a structured claim. The gate grades it against the SOURCE TEXT only.\n"))
    admitted = degraded = rejected = 0
    for title, source, claim in CANNED:
        r = verify(claim, source)
        _show(title, source, claim, r)
        admitted += int(r.admit and not r.degraded)
        degraded += int(r.admit and r.degraded)
        rejected += int(not r.admit)
    print(BOLD("summary: ") + f"{GREEN(str(admitted) + ' admitted')}, "
          f"{YELLOW(str(degraded) + ' degraded')}, {RED(str(rejected) + ' rejected')}  "
          + DIM("— deterministic; re-run for the identical result.\n"))
    return 0


def run_live(provider: str) -> int:
    """Prompt a real cloud model with the production extractor prompt, then grade its output with the gate."""
    import cloud_egress
    from cloud_egress import CloudRequest
    from language.guarded_extract import GUARDED_PROMPT

    key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    api_key = os.environ.get(key_env, "")
    if not api_key:
        print(RED(f"--live needs {key_env} in the environment."))
        return 2
    complete = cloud_egress.anthropic_complete if provider == "anthropic" else cloud_egress.openai_complete

    print(BOLD(f"\nNARS-JARVIS — grounding gate, LIVE mode  ") + DIM(f"(provider={provider})\n"))
    print(DIM("Production extractor prompt over each clause -> the model's own claims -> the same gate.\n"))
    for source in (BREACH, LIAB, PAY, FORCE):
        print(BOLD("• source: ") + CYAN(source))
        res = complete(CloudRequest(system=GUARDED_PROMPT, user=source, max_tokens=768), api_key=api_key)
        if not res.ok:
            print("    " + RED(f"cloud error: {res.error}\n"))
            continue
        text = (res.text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            claims = json.loads(text)
            claims = claims if isinstance(claims, list) else [claims]
        except ValueError:
            print("    " + YELLOW(f"model emitted unparseable output (not the gate's doing): {text[:120]!r}\n"))
            continue
        for claim in claims:
            r = verify(claim, source)
            asserted = claim.get("object") or claim.get("then") or claim.get("value") or claim.get("verb") or ""
            print("    " + DIM("LLM proposed  : ") + f"{claim.get('type')}  ->  {asserted!r}")
            print("    " + DIM("gate verdict  : ") + _verdict_line(r))
            print("    " + DIM("reasons       : ") + ", ".join(r.reasons))
        print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic grounding-gate demo (real verify_gate.py).")
    ap.add_argument("--live", action="store_true", help="prompt a real cloud model, then grade its output")
    ap.add_argument("--provider", choices=("openai", "anthropic"), default="openai")
    args = ap.parse_args()
    return run_live(args.provider) if args.live else run_canned()


if __name__ == "__main__":
    raise SystemExit(main())
