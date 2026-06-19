"""Glue HTML → markdown → chunks together with provenance per chunk."""

from __future__ import annotations

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
    if main_content:
        html = extract_main_content(html)
    md = to_markdown(html)
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
    return ChunkedDocument(url=url, title=md.title, markdown=md.markdown, chunks=chunks)


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
