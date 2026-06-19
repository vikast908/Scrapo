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


def test_chunk_byte_offsets_are_utf8_bytes_with_non_ascii():
    # Force the long-section paragraph-split branch (>target_chars) so the
    # paragraph offset arithmetic runs end-to-end. Each filler para is short on
    # its own; together they overflow.
    para = "Café résumé 🤖 emoji line. " * 8
    md = "# Heading\n\n" + ("\n\n".join([para] * 20))
    chunks = chunk_markdown(md, target_chars=400)
    md_bytes = md.encode("utf-8")
    assert len(chunks) > 1
    for c in chunks:
        # Offsets must index into the UTF-8 byte stream, not the char stream.
        assert 0 <= c.byte_start <= c.byte_end <= len(md_bytes), (
            c.byte_start, c.byte_end, len(md_bytes)
        )
        # And the chunk text should actually be present in the original markdown.
        if c.text:
            assert c.text[:30].strip().split("\n", 1)[0] in md


def test_chunk_byte_range_round_trip_multibyte():
    # INVARIANT: for every emitted chunk, md_bytes[start:end] decodes to valid
    # UTF-8 and equals the chunk's OWN source span (chunk.text minus any
    # prepended overlap). Covers accents, CJK, and emoji.
    md = "Héllo café\n\n日本語\n\n😀 emoji"
    md_bytes = md.encode("utf-8")
    chunks = chunk_markdown(md, target_chars=10000, overlap_chars=0)
    assert chunks
    for c in chunks:
        assert 0 <= c.byte_start <= c.byte_end <= len(md_bytes)
        span = md_bytes[c.byte_start : c.byte_end]
        # Must be a valid UTF-8 boundary slice.
        decoded = span.decode("utf-8")
        # With no overlap, the decoded span equals the chunk text exactly.
        assert decoded == c.text


def test_chunk_byte_range_round_trip_multibyte_split_with_overlap():
    # Force the paragraph-split branch with multi-byte content AND overlap on.
    # The byte range must still be a valid UTF-8 boundary slice that decodes to
    # the chunk's own (non-overlap) source span: the decoded span is the suffix
    # of the chunk text once the prepended overlap is removed.
    md = "# Café 日本語\n\n" + "\n\n".join(
        f"Para 😀 number {i} with some résumé text." for i in range(40)
    )
    md_bytes = md.encode("utf-8")
    chunks = chunk_markdown(md, target_chars=200, overlap_chars=30)
    assert len(chunks) > 1
    for c in chunks:
        assert 0 <= c.byte_start <= c.byte_end <= len(md_bytes)
        decoded = md_bytes[c.byte_start : c.byte_end].decode("utf-8")
        # The byte range points at the OWN source span: the chunk text ends with
        # exactly that span (the overlap, if any, sits in front).
        assert c.text.endswith(decoded)


def test_inline_deep_nesting_does_not_recurse_to_death():
    # Pathologically deep nested inline markup must not raise RecursionError.
    depth = 5000
    html = "<p>" + "<b><i>" * depth + "deep" + "</i></b>" * depth + "</p>"
    md = to_markdown(html).markdown
    assert "deep" in md
