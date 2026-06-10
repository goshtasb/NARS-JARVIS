"""Extractor recall/precision harness (ADR-036 tuning) — measures how often the LIVE 7B maps messy,
real-world phrasing onto the correct closed-vocabulary pair. This is an INTEGRATION eval, not a unit
test: it loads the real GGUF and is gated behind a model + an explicit opt-in, so the offline unit
suite never pays for it.

It wires the extractor to EXACTLY the production callable — `LocalLLM.generate_text` (no grammar),
the same lambda session.py builds — so the numbers reflect the real code path, not a proxy. Run it
before and after a prompt change to get an honest recall/precision delta.

    cd src && RUN_RECALL_EVAL=1 python3 persona/test_extractor_recall.py
    # or point at a specific model: NARS_JARVIS_LLM_GGUF=/path/to.gguf

Caveats kept honest: ~20 phrases is INDICATIVE, not statistically significant. The gold labels below
are developer-assigned judgement calls (a few phrases are genuinely debatable, flagged inline) — the
eval measures agreement with that key, not ground truth from on high.
"""
from __future__ import annotations

import os
from pathlib import Path

from persona import extract
from persona.vocab import term

# (phrase, {expected (predicate, value) pairs}). Empty set = the model should correctly emit nothing.
# Negatives are first-class: they catch the mirror risk of few-shot anchoring — OVER-triggering.
_FORMAT = "format_directive"
_FOCUS = "current_focus"
FIXTURES: list[tuple[str, set[tuple[str, str]]]] = [
    # --- omit_greeting_prose ---
    ("skip the intro, just give me the answer", {(_FORMAT, "omit_greeting_prose")}),
    ("no fluff please", {(_FORMAT, "omit_greeting_prose")}),
    ("cut the pleasantries and get to it", {(_FORMAT, "omit_greeting_prose")}),
    ("what's the bottom line?", {(_FORMAT, "omit_greeting_prose")}),       # debatable label
    ("spare me the preamble", {(_FORMAT, "omit_greeting_prose")}),
    ("just the facts", {(_FORMAT, "omit_greeting_prose")}),               # debatable label
    # --- terse_markdown_tables ---
    ("put that in a table", {(_FORMAT, "terse_markdown_tables")}),
    ("can you lay this out as a table?", {(_FORMAT, "terse_markdown_tables")}),
    ("give me a comparison grid", {(_FORMAT, "terse_markdown_tables")}),
    # --- cite_sources_explicitly ---
    ("cite your sources", {(_FORMAT, "cite_sources_explicitly")}),
    ("where did you get that? link it", {(_FORMAT, "cite_sources_explicitly")}),
    ("back this up with references", {(_FORMAT, "cite_sources_explicitly")}),
    # --- local_development ---
    ("I'm hacking on the local repo all afternoon", {(_FOCUS, "local_development")}),
    ("deep in the codebase right now", {(_FOCUS, "local_development")}),
    ("building and testing locally today", {(_FOCUS, "local_development")}),
    # --- unverified_data_synthesis ---
    ("these figures aren't verified yet, treat them as provisional", {(_FOCUS, "unverified_data_synthesis")}),
    ("I'm pulling together numbers I haven't confirmed", {(_FOCUS, "unverified_data_synthesis")}),
    # --- negatives: the model should stay silent ---
    ("good morning!", set()),
    ("thanks, appreciate it", set()),
    ("what's the weather like?", set()),
    ("tell me a fun fact", set()),
]

_DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "models" / "qwen2.5-7b-instruct-q4_k_m.gguf"


def _resolve_model_path() -> str | None:
    """The GGUF to eval against: the env var if set, else the repo's on-disk 7B if present."""
    env = os.environ.get("NARS_JARVIS_LLM_GGUF")
    if env and Path(env).exists():
        return env
    return str(_DEFAULT_MODEL) if _DEFAULT_MODEL.exists() else None


def _live_generate():
    """Build the EXACT production callable: extractor -> LocalLLM.generate_text (no grammar).
    Mirrors session.py:86-87. Returns None (and the reason) when the model can't be loaded."""
    path = _resolve_model_path()
    if not path:
        return None, f"no GGUF (set NARS_JARVIS_LLM_GGUF or place {_DEFAULT_MODEL.name} in models/)"
    os.environ["NARS_JARVIS_LLM_GGUF"] = path   # LocalLLM reads this in __init__
    try:
        from language import LocalLLM
    except Exception as exc:  # noqa: BLE001 — llama_cpp / model load problems are a skip, not a failure
        return None, f"LocalLLM import failed: {exc}"
    try:
        llm = LocalLLM()
    except Exception as exc:  # noqa: BLE001
        return None, f"model load failed: {exc}"
    return (lambda s, u, mt: llm.generate_text(s, u, max_tokens=mt)), path


def evaluate(generate) -> dict:
    """Run every fixture through the live extractor; aggregate recall/precision over the pair sets."""
    tp = fp = fn = 0
    rows = []
    for phrase, expected in FIXTURES:
        expected_terms = {term(p, v) for (p, v) in expected}
        predicted = {t for (t, _f, _c) in extract([phrase], generate)}
        hit, miss, extra = (expected_terms & predicted), (expected_terms - predicted), (predicted - expected_terms)
        tp += len(hit); fn += len(miss); fp += len(extra)
        rows.append({"phrase": phrase, "expected": expected_terms, "predicted": predicted,
                     "miss": miss, "extra": extra})
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "recall": recall, "precision": precision, "rows": rows}


def _print_report(result: dict, model_path: str) -> None:
    print(f"\n=== Extractor recall eval — {Path(model_path).name} — {len(FIXTURES)} phrases ===\n")
    for r in result["rows"]:
        ok = not r["miss"] and not r["extra"]
        mark = "OK " if ok else "XX "
        exp = ", ".join(sorted(r["expected"])) or "(none)"
        pred = ", ".join(sorted(r["predicted"])) or "(none)"
        print(f"  [{mark}] {r['phrase']!r}")
        if not ok:
            print(f"          expected: {exp}")
            print(f"          got:      {pred}")
            if r["miss"]:
                print(f"          MISSED:   {', '.join(sorted(r['miss']))}")
            if r["extra"]:
                print(f"          EXTRA:    {', '.join(sorted(r['extra']))}")
    print(f"\n  TP={result['tp']}  FP={result['fp']}  FN={result['fn']}")
    print(f"  recall    = {result['recall']:.1%}  (of expected pairs, how many fired)")
    print(f"  precision = {result['precision']:.1%}  (of fired pairs, how many were wanted)")
    print(f"\n  NOTE: n={len(FIXTURES)} is indicative, not statistically significant. "
          "Labels are developer-assigned.\n")


def test_extractor_recall() -> None:
    """Pytest entry — SKIPS unless explicitly opted in AND a model is loadable, so the unit suite
    (which runs model-free) never tries to load a 4.7GB GGUF."""
    if os.environ.get("RUN_RECALL_EVAL") != "1":
        import pytest
        pytest.skip("recall eval is opt-in: set RUN_RECALL_EVAL=1 (loads the live 7B)")
    generate, info = _live_generate()
    if generate is None:
        import pytest
        pytest.skip(f"recall eval skipped — {info}")
    result = evaluate(generate)
    _print_report(result, info)
    # No hard floor asserted: this is a measurement tool, not a gate. Compare runs by hand.
    assert result["tp"] + result["fn"] > 0   # sanity: fixtures actually carry expected pairs


if __name__ == "__main__":
    gen, info = _live_generate()
    if gen is None:
        print(f"SKIP — {info}")
        raise SystemExit(0)
    _print_report(evaluate(gen), info)
