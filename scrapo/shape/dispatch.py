"""Content-type aware document shaping.

Not every URL is an HTML page. This routes a :class:`FetchResult` to the right
shaper based on its ``Content-Type`` (with a little body sniffing as a fallback):

  * ``text/html`` and friends -> the normal selectolax + markdown pipeline
  * ``application/json`` / ``application/ld+json`` -> pretty-printed JSON, with the
    parsed object on ``ChunkedDocument.data``
  * RSS / Atom -> a markdown list of entries, with the parsed items on ``.data``
  * ``application/pdf`` -> extracted text (requires the ``[pdf]`` extra)
  * ``text/plain`` -> the body verbatim
"""

from __future__ import annotations

import io
import json
from typing import Any
from xml.etree import ElementTree as ET

from scrapo.shape.chunker import chunk_markdown
from scrapo.shape.provenance import shape_document
from scrapo.types import Chunk, ChunkedDocument, ProvenanceTag

_HTML_CTYPES = {
    "text/html", "application/xhtml+xml", "application/xml+html", "",
}
_JSON_CTYPES = {"application/json", "application/ld+json", "text/json"}
_FEED_CTYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}
_XMLISH = {"application/xml", "text/xml"}


def shape_fetch(fetch: Any, url: str) -> ChunkedDocument:
    """Shape a FetchResult into a ChunkedDocument, dispatching on content type."""
    ctype = fetch.content_type
    body = fetch.html or ""
    raw = fetch.raw_content

    if raw and raw[:5] == b"%PDF-":
        return _shape_pdf(raw, url)
    if ctype == "application/pdf":
        return _shape_pdf(raw or body.encode("latin-1", "ignore"), url)
    if ctype in _JSON_CTYPES or (ctype not in _HTML_CTYPES and _looks_json(body)):
        return _shape_json(body, url)
    if ctype in _FEED_CTYPES or (ctype in _XMLISH and _looks_feed(body)):
        return _shape_feed(body, url)
    if ctype == "text/plain":
        return _wrap_text(body, url, kind="text", title=None)
    return shape_document(body, url)


def _one_chunk_doc(url: str, title: str | None, markdown: str, *, kind: str, data: Any = None) -> ChunkedDocument:
    chunks: list[Chunk] = []
    for rc in chunk_markdown(markdown):
        prov = ProvenanceTag(
            url=url,
            selector_path=f"{kind}://{'/'.join(rc.heading_trail) or '_root_'}",
            byte_start=rc.byte_start,
            byte_end=rc.byte_end,
            heading_trail=list(rc.heading_trail),
            chunk_hash=rc.hash,
        )
        chunks.append(Chunk(text=rc.text, provenance=prov))
    if not chunks:
        chunks = [Chunk(text=markdown, provenance=ProvenanceTag(url=url, selector_path=f"{kind}://_root_", byte_start=0, byte_end=len(markdown)))]
    return ChunkedDocument(url=url, title=title, markdown=markdown, chunks=chunks, kind=kind, data=data)


def _wrap_text(text: str, url: str, *, kind: str, title: str | None) -> ChunkedDocument:
    return _one_chunk_doc(url, title, text, kind=kind)


def _looks_json(body: str) -> bool:
    s = body.lstrip()[:1]
    return s in ("{", "[")


def _shape_json(body: str, url: str) -> ChunkedDocument:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return shape_document(body, url)
    pretty = json.dumps(data, indent=2, ensure_ascii=False)
    md = f"```json\n{pretty[:60_000]}\n```"
    return _one_chunk_doc(url, None, md, kind="json", data=data)


def _looks_feed(body: str) -> bool:
    head = body.lstrip()[:512].lower()
    return "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _shape_feed(body: str, url: str) -> ChunkedDocument:
    try:
        root = ET.fromstring(body)  # noqa: S314 - feed XML from a page we chose to fetch
    except ET.ParseError:
        return shape_document(body, url)
    items: list[dict[str, str]] = []
    feed_title: str | None = None
    seen_item = False
    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("item", "entry"):
            seen_item = True
            entry: dict[str, str] = {}
            for child in el:
                ctag = _local(child.tag)
                if ctag == "title":
                    entry.setdefault("title", _text(child))
                elif ctag in ("summary", "description", "content"):
                    entry.setdefault("description", _text(child))
                elif ctag == "link":
                    entry["link"] = (child.get("href") or _text(child)).strip()
                elif ctag in ("pubDate", "updated", "published"):
                    entry.setdefault("date", _text(child))
            if entry:
                items.append(entry)
        elif tag == "title" and not seen_item and feed_title is None:
            feed_title = _text(el)
    lines = [f"# {feed_title}"] if feed_title else []
    for it in items:
        link = f" ({it['link']})" if it.get("link") else ""
        lines.append(f"- **{it.get('title', '(untitled)')}**{link}")
        if it.get("description"):
            lines.append(f"  {it['description']}")
    md = "\n".join(lines) if lines else body
    return _one_chunk_doc(url, feed_title, md, kind="feed", data=items)


def _shape_pdf(content: bytes, url: str) -> ChunkedDocument:
    try:
        from pypdf import PdfReader
    except ImportError:
        return _one_chunk_doc(url, None, "_(install `scrapo[pdf]` to extract text from PDF documents)_", kind="pdf")
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except Exception:
        return _one_chunk_doc(url, None, "_(could not read PDF)_", kind="pdf")
    text = "\n\n".join(p for p in pages if p)
    meta_title = None
    try:
        meta_title = (reader.metadata.title or None) if reader.metadata else None
    except Exception:
        meta_title = None
    return _one_chunk_doc(url, meta_title, text or "_(empty PDF)_", kind="pdf")
