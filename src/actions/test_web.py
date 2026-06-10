"""ADR-034 web egress: SSRF guard + the pure parsers, all offline (no network — IP literals + fixture
HTML). The live fetch path is verified separately against the running daemon."""
from actions import web


def test_ssrf_guard_blocks_private_and_nonhttp() -> None:
    assert web.is_ssrf_safe("https://8.8.8.8")              # public IP literal (no DNS) -> allowed
    assert not web.is_ssrf_safe("http://127.0.0.1")         # loopback
    assert not web.is_ssrf_safe("https://192.168.1.50")     # private range
    assert not web.is_ssrf_safe("https://169.254.169.254")  # link-local (cloud metadata)
    assert not web.is_ssrf_safe("file:///etc/passwd")       # non-http scheme
    assert not web.is_ssrf_safe("ftp://example.com")        # non-http scheme
    assert not web.is_ssrf_safe("not a url")


def test_parse_ddg_extracts_top_results_and_decodes_redirect() -> None:
    html = """
    <div class="result__body">
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First</a>
      </h2>
      <a class="result__snippet">First snippet here.</a>
    </div>
    <div class="result__body">
      <h2 class="result__title"><a class="result__a" href="https://plain.example/b">Second</a></h2>
      <a class="result__snippet">Second snippet.</a>
    </div>
    """
    out = web.parse_ddg(html)
    assert "First" in out and "https://example.com/a" in out          # uddg redirect decoded
    assert "First snippet here." in out
    assert "Second" in out and "https://plain.example/b" in out
    assert not out.lstrip().startswith(("{", "["))                    # readable text, not JSON


def test_parse_ddg_reports_when_markup_changes() -> None:
    assert web.parse_ddg("<html><body><p>nothing here</p></body></html>").startswith("[ERROR:")


def test_extract_article_keeps_body_drops_chrome() -> None:
    html = """
    <html><head><title>Big News</title></head><body>
      <nav>Home About Contact Sidebar Links</nav>
      <script>var tracker = 1;</script>
      <article>
        <p>The first paragraph has more than enough words to be treated as genuine article
           content by the readability algorithm so it is retained in the extracted summary.</p>
        <p>A second substantial paragraph with plenty of real text ensures the article body is
           detected and returned cleanly without the surrounding navigation chrome or scripts.</p>
      </article>
      <footer>copyright 2026</footer>
    </body></html>
    """
    out = web.extract_article(html, "https://news.example/story")
    assert "first paragraph" in out and "second substantial paragraph" in out
    assert "var tracker" not in out                          # script stripped
    assert "Source: https://news.example/story" in out


def test_error_strings_pass_through() -> None:
    assert web.parse_ddg("[ERROR: blocked]") == "[ERROR: blocked]"
    assert web.extract_article("[ERROR: blocked]", "u") == "[ERROR: blocked]"
    assert web.extract_links("[ERROR: blocked]", "https://a.com") == []


def test_page_text_fallback_keeps_widget_data() -> None:
    html = """<html><head><title>LA Weather</title><style>.x{}</style></head><body>
      <script>tracker()</script>
      <div class="widget"><span>Now</span><span>83°</span><span>Mostly Clear</span></div>
    </body></html>"""
    out = web.page_text(html, "https://w.example/today")
    assert "83°" in out and "Mostly Clear" in out            # widget data survives (readability drops it)
    assert "tracker()" not in out and ".x{}" not in out      # script/style stripped
    assert out.startswith("Title: LA Weather")
    assert web.page_text("[ERROR: blocked]", "u") == "[ERROR: blocked]"


def test_extract_links_absolutizes_filters_and_dedupes() -> None:
    html = """
    <html><body>
      <a href="/hourly">Hourly forecast</a>
      <a href="https://other.example/radar">Radar &amp; maps</a>
      <a href="/hourly">Hourly forecast (duplicate)</a>
      <a href="#section">same-page anchor</a>
      <a href="javascript:void(0)">scripty</a>
      <a href="mailto:x@y.com">mail</a>
      <a href="/textless"><img src="i.png"/></a>
    </body></html>
    """
    links = web.extract_links(html, "https://weather.example/today")
    assert links == [("Hourly forecast", "https://weather.example/hourly"),
                     ("Radar & maps", "https://other.example/radar")]   # absolutized, deduped, filtered


if __name__ == "__main__":
    test_ssrf_guard_blocks_private_and_nonhttp()
    test_parse_ddg_extracts_top_results_and_decodes_redirect()
    test_parse_ddg_reports_when_markup_changes()
    test_extract_article_keeps_body_drops_chrome()
    test_error_strings_pass_through()
    test_page_text_fallback_keeps_widget_data()
    test_extract_links_absolutizes_filters_and_dedupes()
    print("actions/test_web: OK")
