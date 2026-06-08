"""ADR-015 structural guard: NO module may call subprocess.{Popen,run,call} directly — every spawn
must go through `safespawn` (which scrubs the env + bans shell strings). Python has no runtime access
control, so this AST scan is the enforcement, run as part of the suite/CI."""
import ast
import pathlib

_SRC = pathlib.Path(__file__).resolve().parent
_SPAWN_ATTRS = {"Popen", "run", "call"}
# Only this module is allowed to call subprocess directly (it IS the sanctioned wrapper).
_ALLOWED = {"safespawn.py"}


def _raw_subprocess_lines(source: str) -> list[int]:
    """Line numbers of `subprocess.{Popen,run,call}(...)` CALLS (not annotations/constants)."""
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SPAWN_ATTRS
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            hits.append(node.lineno)
    return hits


def test_no_raw_subprocess_calls_outside_safespawn() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC)
        if (path.name in _ALLOWED or path.name.startswith("test_")
                or "__pycache__" in str(rel)):
            continue
        lines = _raw_subprocess_lines(path.read_text())
        if lines:
            offenders[str(rel)] = lines
    assert not offenders, (
        "Direct subprocess.{Popen,run,call} found — route through safespawn instead: " + str(offenders))


def test_scanner_detects_a_planted_violation() -> None:
    # self-check: the AST scan must flag a real call but not an annotation/constant.
    assert _raw_subprocess_lines("import subprocess\nsubprocess.Popen(['x'])\n") == [2]
    assert _raw_subprocess_lines("x: subprocess.Popen | None = None\n") == []      # annotation ignored
    assert _raw_subprocess_lines("y = subprocess.PIPE\n") == []                    # constant ignored


if __name__ == "__main__":
    test_no_raw_subprocess_calls_outside_safespawn()
    test_scanner_detects_a_planted_violation()
    print("test_no_raw_subprocess: OK")
