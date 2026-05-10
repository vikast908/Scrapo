import io

from pypdf import PdfWriter

from scrapo.shape.dispatch import shape_fetch
from scrapo.types import FetchResult, Tier


def _fetch(*, html="", headers=None, raw=None):
    return FetchResult(
        url="https://e.com/x",
        final_url="https://e.com/x",
        status=200,
        html=html,
        headers=headers or {},
        tier_used=Tier.HTTP,
        raw_content=raw,
    )


def test_json_content_routed():
    doc = shape_fetch(_fetch(html='{"a": 1, "b": [2, 3]}', headers={"content-type": "application/json"}), "https://e.com/x")
    assert doc.kind == "json"
    assert doc.data == {"a": 1, "b": [2, 3]}
    assert '"a": 1' in doc.markdown
    assert doc.chunks


def test_json_sniffed_without_content_type():
    doc = shape_fetch(_fetch(html='  [1, 2, 3]', headers={"content-type": "application/octet-stream"}), "https://e.com/x")
    assert doc.kind == "json"
    assert doc.data == [1, 2, 3]


def test_rss_feed_routed():
    rss = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <title>My Blog</title>
      <item><title>First post</title><link>https://e.com/1</link><description>hello</description></item>
      <item><title>Second post</title><link>https://e.com/2</link></item>
    </channel></rss>"""
    doc = shape_fetch(_fetch(html=rss, headers={"content-type": "application/rss+xml"}), "https://e.com/feed")
    assert doc.kind == "feed"
    assert doc.title == "My Blog"
    assert [it["title"] for it in doc.data] == ["First post", "Second post"]
    assert "First post" in doc.markdown


def test_atom_feed_routed():
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom Feed</title>
      <entry><title>Entry one</title><link href="https://e.com/a"/><summary>sum</summary></entry>
    </feed>"""
    doc = shape_fetch(_fetch(html=atom, headers={"content-type": "application/atom+xml"}), "https://e.com/atom")
    assert doc.kind == "feed"
    assert doc.title == "Atom Feed"
    assert doc.data[0]["title"] == "Entry one"
    assert doc.data[0]["link"] == "https://e.com/a"


def test_text_plain_routed():
    doc = shape_fetch(_fetch(html="line one\nline two", headers={"content-type": "text/plain; charset=utf-8"}), "https://e.com/t")
    assert doc.kind == "text"
    assert doc.markdown == "line one\nline two"


def test_pdf_routed():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    doc = shape_fetch(_fetch(headers={"content-type": "application/pdf"}, raw=buf.getvalue()), "https://e.com/doc.pdf")
    assert doc.kind == "pdf"
    assert isinstance(doc.markdown, str)
    assert doc.chunks


def test_html_is_the_default():
    doc = shape_fetch(_fetch(html="<html><body><h1>Hi</h1><p>text</p></body></html>", headers={"content-type": "text/html"}), "https://e.com/p")
    assert doc.kind == "html"
