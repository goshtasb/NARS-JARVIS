"""Unit tests for read-only file search (ADR-025). An injected fake spawn returns canned mdfind output
— no real Spotlight call — so we assert the argv, the top-N cap, and the empty/no-match paths."""
from actions.files import find_file


class _Spawn:
    """Records argv and returns a CompletedProcess-like object with the given stdout."""
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return type("R", (), {"stdout": self.stdout})()


def test_runs_mdfind_with_argv_query() -> None:
    s = _Spawn("/Users/x/Desktop/Jarvis\n")
    out = find_file("Jarvis", spawn=s)
    assert s.calls == [["mdfind", "-name", "Jarvis"]]          # argv only — no shell
    assert "Found 1 file" in out and "/Users/x/Desktop/Jarvis" in out


def test_caps_results_and_notes_remainder() -> None:
    paths = "\n".join(f"/p/{i}" for i in range(20))
    out = find_file("x", spawn=_Spawn(paths), limit=5)
    assert out.count("- /p/") == 5 and "15 more" in out and "Found 20 files" in out  # token-budget guard


def test_no_matches() -> None:
    assert "No files found" in find_file("nope", spawn=_Spawn(""))


def test_empty_query_does_not_search() -> None:
    s = _Spawn("anything")
    out = find_file("   ", spawn=s)
    assert "What file" in out and s.calls == []                # never spawn on an empty query


if __name__ == "__main__":
    test_runs_mdfind_with_argv_query()
    test_caps_results_and_notes_remainder()
    test_no_matches()
    test_empty_query_does_not_search()
    print("actions/test_files: OK")
