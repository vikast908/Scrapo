from scrapo.shape.chunker import chunk_markdown
from scrapo.shape.markdown import to_markdown
from scrapo.shape.provenance import dedup_chunks, shape_document


def test_markdown_strips_nav_keeps_headings(sample_html):
    doc = to_markdown(sample_html)
    md = doc.markdown
    assert "Hello World" in md
    assert "# Hello World" in md
    assert "## Features" in md
    assert "## Pricing" in md
    assert "nav junk" not in md
    assert "copyright junk" not in md


def test_markdown_renders_table_and_code(sample_html):
    md = to_markdown(sample_html).markdown
    assert "| Plan | Cost |" in md
    assert "Basic" in md and "$19" in md
    assert "```" in md
    assert 'print("hello")' in md


def test_markdown_inline_formatting(sample_html):
    md = to_markdown(sample_html).markdown
    assert "**demo**" in md
    assert "[a topic](/topic)" in md


def test_chunk_by_heading(sample_html):
    md = to_markdown(sample_html).markdown
    chunks = chunk_markdown(md, target_chars=200)
    assert chunks
    trails = [c.heading_trail for c in chunks]
    assert any("Features" in t for t in trails)
    assert any("Pricing" in t for t in trails)


def test_dedup_chunks_removes_duplicates():
    from scrapo.shape.provenance import shape_document

    html = "<html><body><h1>A</h1><p>same body text</p><h1>A</h1><p>same body text</p></body></html>"
    doc = shape_document(html, "https://x")
    deduped = dedup_chunks(doc.chunks)
    assert len(deduped) <= len(doc.chunks)


def test_shape_document_has_provenance(sample_html):
    doc = shape_document(sample_html, "https://example.com")
    assert doc.title and "Demo" in doc.title
    assert doc.chunks
    for c in doc.chunks:
        assert c.provenance.url == "https://example.com"
        assert c.provenance.selector_path.startswith("markdown://")
        assert c.provenance.chunk_hash
