"""Glue HTML → markdown → chunks together with provenance per chunk."""

from __future__ import annotations

from selectolax.parser import HTMLParser

from scrapo.shape.chunker import chunk_markdown
from scrapo.shape.markdown import to_markdown
from scrapo.shape.readability import extract_main_content
from scrapo.types import Chunk, ChunkedDocument, ProvenanceTag


def shape_document(
    html: str,
    url: str,
    *,
    target_chars: int = 2400,
    overlap_chars: int = 100,
    main_content: bool = False,
) -> ChunkedDocument:
    # Resolve the title from the ORIGINAL document. main_content extraction below
    # returns just the article fragment (no <head>), so to_markdown would lose the
    # <title>; this fallback keeps result.title populated regardless.
    source = html
    if main_content:
        html = extract_main_content(html)
    md = to_markdown(html)
    title = md.title or _fallback_title(source)
    raw_chunks = chunk_markdown(md.markdown, target_chars=target_chars, overlap_chars=overlap_chars)
    chunks: list[Chunk] = []
    for rc in raw_chunks:
        anchor = "/".join(rc.heading_trail) or "_root_"
        prov = ProvenanceTag(
            url=url,
            selector_path=f"markdown://{anchor}",
            byte_start=rc.byte_start,
            byte_end=rc.byte_end,
            heading_trail=list(rc.heading_trail),
            chunk_hash=rc.hash,
        )
        chunks.append(Chunk(text=rc.text, provenance=prov))
    return ChunkedDocument(url=url, title=title, markdown=md.markdown, chunks=chunks)


def _fallback_title(html: str) -> str | None:
    """Best-effort document title from the full HTML: og:title, then <title>,
    then the first <h1>. Used when the markdown converter found none (e.g. after
    main-content extraction stripped the <head>). Never raises."""
    if not html or not html.strip():
        return None
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001 - unparseable markup → no title
        return None
    for sel in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        node = tree.css_first(sel)
        if node is not None:
            content = (node.attributes.get("content") or "").strip()
            if content:
                return content
    title_node = tree.css_first("title")
    if title_node is not None:
        text = title_node.text(strip=True)
        if text:
            return text
    h1 = tree.css_first("h1")
    if h1 is not None:
        text = h1.text(strip=True)
        if text:
            return text
    return None


def dedup_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Return chunks with duplicate text bodies removed (keeps first occurrence)."""
    seen: set[str] = set()
    out: list[Chunk] = []
    for c in chunks:
        h = c.hash
        if h in seen:
            continue
        seen.add(h)
        out.append(c)
    return out
