from scrapo.shape.markdown import to_markdown
from scrapo.shape.readability import extract_main_content

_ARTICLE_PARA_1 = (
    "The history of distributed systems begins with the realization that a "
    "single machine can only do so much before it falls over under load."
)
_ARTICLE_PARA_2 = (
    "Consensus algorithms such as Paxos and Raft let a cluster of unreliable "
    "machines agree on a single value even when some of them crash."
)
_ARTICLE_PARA_3 = (
    "In practice, most engineers reach for a managed datastore long before "
    "they ever need to implement a consensus protocol from first principles."
)


def test_semantic_article_strips_boilerplate() -> None:
    html = f"""
    <html><body>
      <nav class="nav">Home Products About Contact nav junk links</nav>
      <aside class="sidebar">Sidebar junk recommended widgets advertising</aside>
      <div class="ad">Buy now! Limited offer ad junk content here</div>
      <article>
        <h1>Distributed Systems</h1>
        <p>{_ARTICLE_PARA_1}</p>
        <p>{_ARTICLE_PARA_2}</p>
        <p>{_ARTICLE_PARA_3}</p>
      </article>
      <footer class="footer">Copyright 2026 footer junk privacy terms</footer>
    </body></html>
    """
    out = extract_main_content(html)
    assert _ARTICLE_PARA_1 in out
    assert _ARTICLE_PARA_2 in out
    assert _ARTICLE_PARA_3 in out
    assert "nav junk" not in out
    assert "Sidebar junk" not in out
    assert "ad junk" not in out
    assert "footer junk" not in out

    # And it survives the markdown pass cleanly.
    md = to_markdown(out).markdown
    assert "Distributed Systems" in md
    assert "nav junk" not in md
    assert "footer junk" not in md


def test_high_density_div_without_semantic_tag() -> None:
    html = f"""
    <html><body>
      <div id="header" class="masthead">Site Title menu nav header junk</div>
      <div class="sidebar">Related posts sidebar junk promo links list here</div>
      <div class="post-content">
        <h1>Raft Explained</h1>
        <p>{_ARTICLE_PARA_1}</p>
        <p>{_ARTICLE_PARA_2}</p>
        <p>{_ARTICLE_PARA_3}</p>
      </div>
      <div class="footer">footer junk copyright social share buttons</div>
    </body></html>
    """
    out = extract_main_content(html)
    assert _ARTICLE_PARA_1 in out
    assert _ARTICLE_PARA_2 in out
    assert "header junk" not in out
    assert "sidebar junk" not in out
    assert "footer junk" not in out


def test_link_heavy_menu_not_chosen_over_text_block() -> None:
    links = " ".join(
        f'<a href="/page/{i}">Navigate to interesting section number {i} here</a>'
        for i in range(40)
    )
    html = f"""
    <html><body>
      <div class="menu">{links}</div>
      <div class="story">
        <p>{_ARTICLE_PARA_1}</p>
        <p>{_ARTICLE_PARA_2}</p>
        <p>{_ARTICLE_PARA_3}</p>
      </div>
    </body></html>
    """
    out = extract_main_content(html)
    assert _ARTICLE_PARA_1 in out
    assert _ARTICLE_PARA_2 in out
    # The link menu must not be the chosen subtree.
    assert "Navigate to interesting section number 0" not in out


def test_empty_string_returned_unchanged() -> None:
    assert extract_main_content("") == ""


def test_whitespace_returned_unchanged() -> None:
    assert extract_main_content("   \n  ") == "   \n  "


def test_malformed_html_never_raises_and_preserves_content() -> None:
    bad = "<not html"
    out = extract_main_content(bad)
    # Must not raise; content preserved (returned as-is in the no-region case).
    assert "not html" in out


def test_no_clear_main_region_falls_back() -> None:
    # A page that is essentially all tiny boilerplate -> nothing trustworthy.
    html = "<html><body><nav class='nav'>a</nav><footer class='footer'>b</footer></body></html>"
    out = extract_main_content(html)
    assert out == html


def test_role_main_is_recognised() -> None:
    html = f"""
    <html><body>
      <div class="nav">nav junk menu links here for navigation purposes</div>
      <div role="main">
        <p>{_ARTICLE_PARA_1}</p>
        <p>{_ARTICLE_PARA_2}</p>
        <p>{_ARTICLE_PARA_3}</p>
      </div>
    </body></html>
    """
    out = extract_main_content(html)
    assert _ARTICLE_PARA_1 in out
    assert "nav junk" not in out
