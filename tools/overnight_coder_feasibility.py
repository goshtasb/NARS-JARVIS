#!/usr/bin/env python3
"""Overnight-coder feasibility harness (pre-ADR-043). STANDALONE — touches no daemon state, no
jarvis.db; freeze-compatible by construction.

The question it answers with a NUMBER instead of an opinion: can a local 7B, given a distilled
few-shot TDD micro-prompt (the agent-skills discipline compressed for a 4096-token window), write
pytest tests for OUR OWN pure functions that actually run and pass? The result gates whether ADR-043
(overnight test/doc writing) is worth drafting, and whether the dedicated coder model
(qwen2.5-coder-7b) is the unlock vs. the conversational 7B.

Per target function: feed source (+ the module constants it references) to the model -> extract the
generated test file -> execute it via pytest in a throwaway temp dir (subprocess, hard timeout).
Metrics: generated / runnable (pytest executed) / passed (exit 0) / assert count / calls-the-target.
Every generated file is kept under $TMPDIR/coder_feasibility/<model>/ for inspection — the report is
auditable, not just a summary.

HONEST LIMITS: the "sandbox" is a temp cwd + timeout, NOT an OS-level sandbox — generated code runs
with user privileges. Acceptable here because the inputs are our own function sources (not hostile
text) and a human is watching; the production overnight design still REQUIRES the real sandbox
(sandbox-exec / OmniGlass tier) before any unattended run. n=10 functions is indicative, not proof.

Usage:
    python3 tools/overnight_coder_feasibility.py /path/to/model.gguf
"""
from __future__ import annotations

import inspect
import importlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# (module, function, [module-level constants whose source the function's behavior depends on])
TARGETS: list[tuple[str, str, list[str]]] = [
    ("context.volatile", "is_volatile", ["_VOLATILE_PATTERNS"]),
    ("actions.diagnostics", "parse_volume_settings", []),
    ("actions.diagnostics", "anomaly_flags", ["CPU_HIGH", "MEM_HIGH", "DISK_HIGH", "BATTERY_LOW"]),
    ("persona.vocab", "split_term", []),
    ("persona.vocab", "term", []),
    ("research.agent", "parse_step", ["_STEP_RE"]),
    ("research.agent", "data_window", []),
    ("research.agent", "links_from_results", []),
    ("research.agent", "split_browse", ["_MENU_LINE_RE"]),
    ("actions.web", "_decode_ddg", []),
]

_PROMPT = (
    "You are a senior engineer writing pytest unit tests. You will be given ONE Python function. "
    "Write a complete pytest test FILE for it.\n"
    "Rules:\n"
    "- Import the function EXACTLY as: from {module} import {name}\n"
    "- Test only the behavior visible in the source: normal cases AND edge cases (empty input, "
    "garbage input).\n"
    "- Plain pytest functions, multiple asserts. No network, no files, no mocks, no classes.\n"
    "- Output ONLY the Python code. No explanations, no markdown fences.\n\n"
    "Example — for `def add(a: int, b: int) -> int: return a + b` in module `mymod`:\n"
    "from mymod import add\n\n"
    "def test_add_basic():\n"
    "    assert add(2, 3) == 5\n"
    "    assert add(0, 0) == 0\n\n"
    "def test_add_negative():\n"
    "    assert add(-1, 1) == 0\n"
)


def source_block(module_src: str, var: str) -> str:
    """A top-level `VAR = …` assignment block (multi-line, paren-balanced). '' if absent. Pure."""
    m = re.search(rf"^{re.escape(var)}\s*[:=]", module_src, re.M)
    if not m:
        return ""
    depth, out = 0, []
    for line in module_src[m.start():].splitlines():
        out.append(line)
        depth += line.count("(") + line.count("[") + line.count("{")
        depth -= line.count(")") + line.count("]") + line.count("}")
        if depth <= 0:
            break
    return "\n".join(out)


def build_user(module: str, name: str, extras: list[str]) -> str:
    mod = importlib.import_module(module)
    func_src = inspect.getsource(getattr(mod, name))
    extra_src = "\n".join(s for v in extras if (s := source_block(inspect.getsource(mod), v)))
    ctx = (f"Module constants the function uses:\n{extra_src}\n\n" if extra_src else "")
    return f"Module: {module}\n\n{ctx}Function:\n{func_src}"


def extract_code(reply: str) -> str:
    """Lift code from the reply; tolerate ``` fences despite the instruction (7Bs add them)."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip()


def run_in_sandbox(code: str, path: Path) -> tuple[bool, bool]:
    """Write the generated test and run pytest on it in a temp cwd. -> (runnable, passed)."""
    path.write_text(code)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", str(path), "-q", "--no-header",
                                "-p", "no:cacheprovider"],
                               cwd=tmp, env={**os.environ, "PYTHONPATH": str(SRC)},
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False, False
        return r.returncode in (0, 1), r.returncode == 0   # 0 pass, 1 ran-but-failed, ≥2 broken


def main(model_path: str) -> None:
    from language import LocalLLM
    llm = LocalLLM(model_path=model_path)
    tag = Path(model_path).stem
    outdir = Path(tempfile.gettempdir()) / "coder_feasibility" / tag
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for module, name, extras in TARGETS:
        user = build_user(module, name, extras)
        try:
            reply = llm.generate_text(_PROMPT.format(module=module, name=name), user, max_tokens=700)
        except Exception as exc:  # noqa: BLE001 — a generation failure is a data point, not a crash
            rows.append({"name": name, "gen": False, "run": False, "ok": False, "asserts": 0,
                         "calls": False, "note": f"generation failed: {exc}"})
            continue
        code = extract_code(reply)
        gen = bool(code) and "def test" in code
        asserts = code.count("assert ")
        calls = name in code.replace(f"import {name}", "")     # used beyond the import line
        runnable = passed = False
        if gen:
            runnable, passed = run_in_sandbox(code, outdir / f"test_gen_{name}.py")
        rows.append({"name": name, "gen": gen, "run": runnable, "ok": passed,
                     "asserts": asserts, "calls": calls, "note": ""})
        print(f"  {name:24} gen={gen!s:5} runnable={runnable!s:5} passed={passed!s:5} "
              f"asserts={asserts:2} calls_target={calls}")
    n = len(rows)
    print(f"\n=== {tag} — {n} pure functions ===")
    print(f"  generated : {sum(r['gen'] for r in rows)}/{n}")
    print(f"  runnable  : {sum(r['run'] for r in rows)}/{n}   (pytest executed the file)")
    print(f"  passed    : {sum(r['ok'] for r in rows)}/{n}   (all generated asserts green)")
    print(f"  mean asserts in runnable files: "
          f"{(sum(r['asserts'] for r in rows if r['run']) / max(1, sum(r['run'] for r in rows))):.1f}")
    print(f"  generated files kept in: {outdir}")
    print("  NOTE: n=10 is indicative, not statistical proof. 'passed' can include weak asserts — "
          "inspect the kept files before drawing conclusions.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: overnight_coder_feasibility.py /path/to/model.gguf")
    main(sys.argv[1])
