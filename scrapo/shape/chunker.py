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
    paragraphs = text.split("\n\n")
    out: list[tuple[str, int, int]] = []
    buf: list[str] = []
    buf_len = 0
    cursor = 0
    chunk_start = 0
    for para in paragraphs:
        para_with_sep = para + "\n\n"
        if buf_len + len(para_with_sep) > target_chars and buf:
            joined = "".join(buf)
            out.append((joined, chunk_start, chunk_start + len(joined)))
            tail = joined[-overlap_chars:] if overlap_chars else ""
            chunk_start = chunk_start + len(joined) - len(tail)
            buf = [tail] if tail else []
            buf_len = len(tail)
        buf.append(para_with_sep)
        buf_len += len(para_with_sep)
        cursor += len(para_with_sep)
    if buf:
        joined = "".join(buf)
        out.append((joined, chunk_start, chunk_start + len(joined)))
    return out
