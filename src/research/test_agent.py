"""ADR-039 research loop: pure parsers + the bounded loop with fake generate/perform — no model, no
network. The injection bound (index-only link selection) and every cap are asserted here."""
from research import links_from_results, parse_step, run_research, split_browse
from research import agent


def test_parse_step_decisions() -> None:
    assert parse_step("OPEN 3") == ("open", "3")
    assert parse_step("  open 12: the weather one") == ("open", "12")
    assert parse_step("SEARCH current LA temperature") == ("search", "current LA temperature")
    assert parse_step("ANSWER") == ("answer", "")
    assert parse_step("Sure.\nOPEN 2") == ("open", "2")              # directive on its own line is found
    assert parse_step("I think I will OPEN 2") == ("answer", "")     # STRICT: mid-prose never fires
    assert parse_step("blah blah no directive") == ("answer", "")    # unparseable -> stop, never free-run
    assert parse_step("OPEN evil.com") == ("answer", "")             # OPEN takes a number, not a URL
    assert parse_step("") == ("answer", "")


def test_links_from_results_lifts_numbered_urls() -> None:
    text = ("1. Weather Forecast for LA - The Weather Channel\n"
            "   Today's forecast, conditions and radar\n"
            "   https://weather.com/la/today\n"
            "2. LA Hourly | AccuWeather\n"
            "   https://accuweather.com/la\n")
    assert links_from_results(text) == [
        ("Weather Forecast for LA - The Weather Channel", "https://weather.com/la/today"),
        ("LA Hourly | AccuWeather", "https://accuweather.com/la")]
    assert links_from_results("[ERROR: no results]") == []


def test_split_browse_separates_article_and_links() -> None:
    article, links = split_browse("Title: T\nSource: https://a.com\n\nBody text.\n\nLINKS:\n"
                                  "1. Hourly — https://a.com/hourly\n2. Radar — https://a.com/radar")
    assert article.startswith("Title: T") and "Body text." in article
    assert links == [("Hourly", "https://a.com/hourly"), ("Radar", "https://a.com/radar")]
    article, links = split_browse("Just text, no links section")
    assert article == "Just text, no links section" and links == []


def _results(*pairs: tuple[str, str]) -> str:
    return "\n".join(f"{i}. {t}\n   snippet\n   {u}" for i, (t, u) in enumerate(pairs, 1))


def test_loop_opens_chosen_link_and_synthesizes() -> None:
    performed: list[tuple[str, str]] = []
    def perform(name: str, arg: str) -> str:
        performed.append((name, arg))
        if name == "web_lookup":
            return _results(("WeatherSite", "https://w.com/today"), ("Other", "https://o.com"))
        return "Title: W\nSource: https://w.com/today\n\nLA: 76F sunny.\n\nLINKS:\n1. Hourly — https://w.com/h"
    replies = iter(["OPEN 1", "ANSWER", "It is 76F and sunny in LA (per w.com)."])
    def generate(system: str, user: str, max_tokens: int) -> str:
        return next(replies)
    answer, errors = run_research("weather in LA?", [("web_lookup", "weather in LA")], generate, perform)
    assert performed == [("web_lookup", "weather in LA"), ("browse_page", "https://w.com/today")]
    assert answer == "It is 76F and sunny in LA (per w.com)." and errors == []


def test_injection_bound_model_cannot_mint_urls() -> None:
    """OPEN with an out-of-menu index (or a URL) never fetches — the menu is the whole universe."""
    performed: list[tuple[str, str]] = []
    def perform(name: str, arg: str) -> str:
        performed.append((name, arg))
        return _results(("Only", "https://only.com"))
    def generate(system: str, user: str, max_tokens: int) -> str:
        return "OPEN 99" if max_tokens == 32 else "synthesized"
    answer, _ = run_research("q", [("web_lookup", "q")], generate, perform)
    assert all(name == "web_lookup" for name, _a in performed)        # no fetch from the bad pick
    assert answer == "synthesized"


def test_caps_bound_the_loop() -> None:
    counts = {"web_lookup": 0, "browse_page": 0}
    def perform(name: str, arg: str) -> str:
        counts[name] += 1
        n = counts["web_lookup"] * 10 + counts["browse_page"]
        return (_results((f"R{n}", f"https://r{n}.com")) if name == "web_lookup" else
                f"Title: T\nSource: {arg}\n\ntext\n\nLINKS:\n1. more{n} — https://m{n}.com")
    def generate(system: str, user: str, max_tokens: int) -> str:
        return "OPEN 1" if max_tokens == 32 else "done"               # always wants to keep clicking
    answer, _ = run_research("q", [("web_lookup", "q")], generate, perform)
    assert counts["browse_page"] == agent.MAX_OPENS                  # the cap, not the model, stopped it
    assert answer == "done"


def test_wall_clock_bound() -> None:
    t = {"now": 0.0}
    def clock() -> float:
        t["now"] += 200.0                                            # the first loop check is past deadline
        return t["now"]
    def perform(name: str, arg: str) -> str:
        return _results(("A", "https://a.com"))
    calls = {"n": 0}
    def generate(system: str, user: str, max_tokens: int) -> str:
        calls["n"] += 1
        return "OPEN 1" if max_tokens == 32 else "late answer"
    answer, _ = run_research("q", [("web_lookup", "q")], generate, perform, clock=clock)
    assert answer == "late answer" and calls["n"] == 1               # only synthesis ran — no decisions


def test_all_errors_surface_honestly() -> None:
    def perform(name: str, arg: str) -> str:
        return "[ERROR: target rate-limited or blocked after retries]"
    def generate(system: str, user: str, max_tokens: int) -> str:
        raise AssertionError("no notes -> no model calls at all")
    answer, errors = run_research("q", [("web_lookup", "q")], generate, perform)
    assert answer is None and errors and errors[0].startswith("[ERROR:")


def test_read_article_seed_opens_directly_and_model_failure_degrades() -> None:
    def perform(name: str, arg: str) -> str:
        assert name == "browse_page" and arg == "https://x.com/page"
        return "Title: X\nSource: https://x.com/page\n\nfacts here"
    def generate(system: str, user: str, max_tokens: int) -> str:
        raise RuntimeError("model died")
    answer, errors = run_research("q", [("read_article", "https://x.com/page")], generate, perform)
    assert answer is not None and "facts here" in answer and errors == []   # raw-notes fallback


if __name__ == "__main__":
    test_parse_step_decisions()
    test_links_from_results_lifts_numbered_urls()
    test_split_browse_separates_article_and_links()
    test_loop_opens_chosen_link_and_synthesizes()
    test_injection_bound_model_cannot_mint_urls()
    test_caps_bound_the_loop()
    test_wall_clock_bound()
    test_all_errors_surface_honestly()
    test_read_article_seed_opens_directly_and_model_failure_degrades()
    print("research/test_agent: OK")
