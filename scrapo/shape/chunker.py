"""Heading-aware markdown chunker.

Splits along the heading hierarchy, falling back to paragraph splits when a
single section exceeds the target token budget. Each chunk records its
heading_trail so downstream LLMs and search indexes know its document context.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(slots=True)
class RawChunk:
    text: str
    heading_trail: list[str]
    byte_start: int
    byte_end: int

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", errors="replace")).hexdigest()


def chunk_markdown(
    md: str,
    *,
    target_chars: int = 2400,
    overlap_chars: int = 100,
) -> list[RawChunk]:
    if not md.strip():
        return []
    sections = _split_by_heading(md)
    chunks: list[RawChunk] = []
    for section_text, trail, start_byte in sections:
        if len(section_text) <= target_chars:
            chunks.append(
                RawChunk(
                    text=section_text.strip(),
                    heading_trail=list(trail),
                    byte_start=start_byte,
                    byte_end=start_byte + len(section_text.encode("utf-8")),
                )
            )
            continue
        for piece, p_start, p_end in _split_paragraphs(
            section_text, target_chars, overlap_chars
        ):
            chunks.append(
                RawChunk(
                    text=piece.strip(),
                    heading_trail=list(trail),
                    byte_start=start_byte + p_start,
                    byte_end=start_byte + p_end,
                )
            )
    return [c for c in chunks if c.text]


def _split_by_heading(md: str) -> list[tuple[str, list[str], int]]:
    lines = md.splitlines(keepends=True)
    sections: list[tuple[str, list[str], int]] = []
    cur_lines: list[str] = []
    cur_trail: list[str] = []
    cur_start_byte = 0
    seen_bytes = 0

    def flush(byte_at: int) -> None:
        if cur_lines:
            sections.append(("".join(cur_lines), list(cur_trail), cur_start_byte))

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush(seen_bytes)
            level = len(m.group(1))
            title = m.group(2).strip()
            cur_trail = cur_trail[: level - 1] + [title]
            cur_lines = [line]
            cur_start_byte = seen_bytes
        else:
            cur_lines.append(line)
        seen_bytes += len(line.encode("utf-8"))
    flush(seen_bytes)
    return sections


def _split_paragraphs(
    text: str, target_chars: int, overlap_chars: int
) -> list[tuple[str, int, int]]:
    """Return ``(piece, byte_start, byte_end)`` triples whose offsets index into
    ``text.encode("utf-8")``. The caller adds these to the section's own UTF-8
    byte_start, so any mismatch silently corrupts every provenance range that
    contains a non-ASCII character.

    Each emitted chunk's byte range maps to the actual paragraphs that produced
    it; the optional ``overlap_chars`` tail is prepended to the *next* chunk's
    text purely as LLM context — it doesn't extend the byte range backwards (a
    mid-paragraph tail would need a sub-paragraph byte index we don't track).
    """
    sep = "\n\n"
    sep_bytes = len(sep.encode("utf-8"))
    paragraphs = text.split(sep)

    # Pre-compute byte offsets of each paragraph within ``text``.
    para_byte_start: list[int] = []
    para_byte_len: list[int] = []
    cursor = 0
    for i, para in enumerate(paragraphs):
        para_byte_start.append(cursor)
        n = len(para.encode("utf-8"))
        para_byte_len.append(n)
        cursor += n
        if i < len(paragraphs) - 1:
            cursor += sep_bytes

    out: list[tuple[str, int, int]] = []
    buf_paras: list[str] = []
    buf_first_idx = 0
    buf_chars = 0
    pending_tail = ""

    def flush() -> None:
        nonlocal pending_tail
        if not buf_paras:
            return
        last_idx = buf_first_idx + len(buf_paras) - 1
        start_b = para_byte_start[buf_first_idx]
        end_b = para_byte_start[last_idx] + para_byte_len[last_idx]
        body = sep.join(buf_paras)
        piece = pending_tail + body if pending_tail else body
        out.append((piece, start_b, end_b))
        pending_tail = body[-overlap_chars:] if overlap_chars else ""

    for i, para in enumerate(paragraphs):
        cost = len(para) + len(sep)  # char budget; bytes tracked separately
        if buf_chars + cost > target_chars and buf_paras:
            flush()
            buf_paras = []
            buf_chars = 0
        if not buf_paras:
            buf_first_idx = i
        buf_paras.append(para)
        buf_chars += cost
    flush()
    return out
