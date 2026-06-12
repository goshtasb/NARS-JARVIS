"""Natural-language Intent Router (ADR-054) — Functional Core (S-02), pure.

Bridges the non-deterministic 7B to the closed catalog. Two pure pieces, both testable with no model
and no I/O:

- `build_intent_grammar(action_names)` → a GBNF string that PHYSICALLY constrains the model's output to
  `{"action": <enum>, "arg": <string>, "timing": null | {kind, value}}`. The action is an alternation
  of the live catalog names **plus a "none" sentinel** (the escape hatch — without it a closed enum is a
  forcing function for hallucinations). The arg string uses the standard JSON `[^"\\]` body, so paths
  and URLs (`/ . : ~ @ ? = &`) are permitted by construction.
- `validate_intent(payload, …)` → the Interception Gate: semantic checks BEFORE anything is committed.
  It rejects the structurally doomed (missing arg, `none`, malformed/out-of-range timing) and passes a
  concrete-but-possibly-wrong target through (a nonexistent path fails visibly at execution, ADR-053 —
  the gate never touches the filesystem, so it stays pure and race-free).

Timing is emitted as a RELATIVE classification (`in_minutes` / `at_clock_hour`), never an absolute
epoch — the model does no arithmetic and the client resolves it against local time, preserving the
timezone-free daemon contract (ADR-053).
"""
from __future__ import annotations

from typing import Callable

NONE_ACTION = "none"
_TIMING_KINDS = ("now", "in_minutes", "at_clock_hour")
_MAX_SCHEDULE_MINUTES = 7 * 24 * 60          # a week — beyond this is almost certainly a parse error

# GBNF skeleton; __ACTION_ENUM__ is filled with the live catalog. Raw string: backslashes are literal,
# exactly as llama.cpp's GBNF wants (`\"` = a quote inside a terminal; `[^"\\]` = any char but "/\).
_GRAMMAR_TEMPLATE = r'''root   ::= ws "{" ws "\"action\"" ws ":" ws action ws "," ws "\"arg\"" ws ":" ws string ws "," ws "\"timing\"" ws ":" ws timing ws "}" ws
action ::= __ACTION_ENUM__
string ::= "\"" ( strchar | escape )* "\""
strchar ::= [^"\\]
escape ::= "\\" ["\\/bfnrt]
timing ::= "null" | "{" ws "\"kind\"" ws ":" ws kind ws "," ws "\"value\"" ws ":" ws int ws "}" ws
kind   ::= "\"now\"" | "\"in_minutes\"" | "\"at_clock_hour\""
int    ::= "-"? [0-9]+
ws     ::= [ \t\n]*
'''


def build_intent_grammar(action_names: list[str], include_none: bool = True) -> str:
    """Compile a GBNF that constrains the action field to `action_names` (∪ `{none}` when
    `include_none`). The `/` override pins a verb by passing a single name with `include_none=False`,
    so the user's explicit choice can't be overridden back to `none`."""
    members = [f'"\\"{n}\\""' for n in action_names]
    if include_none or not members:                       # always keep at least the sentinel -> valid
        members.append(f'"\\"{NONE_ACTION}\\""')
    return _GRAMMAR_TEMPLATE.replace("__ACTION_ENUM__", " | ".join(members))


def build_system_prompt(catalog_lines: list[str], now_local: str) -> str:
    """The router instruction. `catalog_lines` are 'name — label' so the model can MAP intent to a verb
    (the grammar constrains the output set; the prompt supplies the meaning). `now_local` injects the
    current local time so the model can classify relative timing without knowing the timezone."""
    actions = "\n".join(f"  {ln}" for ln in catalog_lines)
    return (
        "You translate one user request into a single JSON job for a local assistant.\n"
        f"The current local time is {now_local}.\n"
        "Choose exactly one action from this list (use \"none\" if the request matches none of them):\n"
        f"{actions}\n"
        '  none — the request does not match any available action\n\n'
        "Output ONLY a JSON object: {\"action\": <one of the names>, \"arg\": <the target, e.g. a file "
        "path or URL, or \"\" if the action takes none>, \"timing\": <null for now, or "
        "{\"kind\":\"in_minutes\",\"value\":N} / {\"kind\":\"at_clock_hour\",\"value\":H 0-23} / "
        "{\"kind\":\"now\",\"value\":0}>}. Never invent an action that is not listed."
    )


def validate_intent(payload: dict, valid_actions: set[str], arg_required: set[str]
                    ) -> tuple[bool, dict]:
    """The Interception Gate (pure). Returns (True, normalized-intent) or (False, {clarify}).

    Normalized intent: {"action", "arg", "timing"} where timing is None or {"kind","value"}. Does NOT
    check the filesystem — a nonexistent path is a runtime/Canvas concern, not a structural one."""
    action = str(payload.get("action", "")).strip()
    arg = str(payload.get("arg", "") or "").strip()

    if action == NONE_ACTION or not action:
        return False, {"clarify": "I couldn't match that to something I can do. Can you rephrase it?"}
    if action not in valid_actions:                       # grammar should prevent this; defense in depth
        return False, {"clarify": f"I don't have an action called \"{action}\"."}
    if action in arg_required and not arg:
        return False, {"clarify": f"\"{action}\" needs a target (e.g. a file path or URL). Which one?"}

    ok, timing = _validate_timing(payload.get("timing"))
    if not ok:
        return False, {"clarify": "I didn't understand when to run that — try \"now\", \"in 2 hours\", "
                                  "or \"at 11pm\"."}
    return True, {"action": action, "arg": arg, "timing": timing}


def _validate_timing(timing: object) -> tuple[bool, object]:
    """None (or {kind:now}) -> run now. {in_minutes,N} / {at_clock_hour,H} validated and bounded.
    Returns (ok, normalized-timing-or-None). The CLIENT resolves a non-None timing to an absolute epoch."""
    if timing is None:
        return True, None
    if not isinstance(timing, dict):
        return False, None
    kind, value = timing.get("kind"), timing.get("value", 0)
    if kind not in _TIMING_KINDS or not isinstance(value, (int, float)):
        return False, None
    value = int(value)
    if kind == "now":
        return True, None                                 # normalize "now" to None (manual / Run Now)
    if kind == "in_minutes":
        return (True, {"kind": kind, "value": value}) if 1 <= value <= _MAX_SCHEDULE_MINUTES else (False, None)
    if kind == "at_clock_hour":
        return (True, {"kind": kind, "value": value}) if 0 <= value <= 23 else (False, None)
    return False, None
