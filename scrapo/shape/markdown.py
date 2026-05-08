"""HTML → Markdown conversion using selectolax for parsing.

Preserves: headings (with anchor IDs), tables, code blocks, lists, links,
image alt text. Strips: scripts, styles, navs, footers, ads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser, Node

_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "form",
    "head",
    "header",
    "footer",
    "nav",
    "aside",
    "menu",
    "meta",
    "link",
}

_SKIP_CLASS_RE = re.compile(
    r"\b(ads?|advertisement|sidebar|cookie|banner|popup|modal|share|social|breadcrumb|nav)\b",
    re.I,
)


@dataclass(slots=True)
class MarkdownDoc:
    title: str | None
    markdown: str


def to_markdown(html: str) -> MarkdownDoc:
    if not html.strip():
        return MarkdownDoc(title=None, markdown="")

    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else None

    body = tree.css_first("body") or tree.root
    if body is None:
        return MarkdownDoc(title=title, markdown="")

    out: list[str] = []
    _walk(body, out)
    md = _post_process("\n".join(out))
    return MarkdownDoc(title=title, markdown=md)


def _walk(node: Node, out: list[str], list_depth: int = 0) -> None:
    tag = node.tag
    if tag in _SKIP_TAGS:
        return
    classes = node.attributes.get("class") if node.attributes else None
    if classes and _SKIP_CLASS_RE.search(classes):
        return

    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(tag[1])
        text = _inline(node)
        if text:
            out.append("")
            out.append("#" * level + " " + text)
            out.append("")
        return

    if tag == "p":
        text = _inline(node)
        if text:
            out.append("")
            out.append(text)
            out.append("")
        return

    if tag == "br":
        out.append("")
        return

    if tag == "hr":
        out.append("")
        out.append("---")
        out.append("")
        return

    if tag in {"ul", "ol"}:
        out.append("")
        idx = 1
        for child in node.iter():
            if child.tag == "li":
                text = _inline(child)
                if not text:
                    continue
                marker = f"{idx}." if tag == "ol" else "-"
                indent = "  " * list_depth
                out.append(f"{indent}{marker} {text}")
                idx += 1
        out.append("")
        return

    if tag == "blockquote":
        text = _inline(node)
        if text:
            out.append("")
            out.append("> " + text.replace("\n", "\n> "))
            out.append("")
        return

    if tag == "pre":
        code = node.css_first("code")
        text = (code.text() if code else node.text()).rstrip()
        if text:
            out.append("")
            out.append("```")
            out.append(text)
            out.append("```")
            out.append("")
        return

    if tag == "table":
        md = _render_table(node)
        if md:
            out.append("")
            out.append(md)
            out.append("")
        return

    if tag == "img":
        alt = (node.attributes.get("alt") or "").strip() if node.attributes else ""
        src = (node.attributes.get("src") or "").strip() if node.attributes else ""
        if src:
            out.append(f"![{alt}]({src})")
        return

    for child in node.iter():
        _walk(child, out, list_depth)


def _inline(node: Node) -> str:
    parts: list[str] = []
    for child in node.iter(include_text=True):
        if isinstance(child, str):
            parts.append(child)
            continue
        tag = child.tag
        if tag == "-text":
            parts.append(child.text() or "")
        elif tag in {"strong", "b"}:
            parts.append(f"**{_inline(child)}**")
        elif tag in {"em", "i"}:
            parts.append(f"*{_inline(child)}*")
        elif tag in {"code"}:
            parts.append(f"`{child.text() or ''}`")
        elif tag == "a":
            href = (child.attributes.get("href") or "").strip() if child.attributes else ""
            inner = _inline(child).strip()
            if inner and href:
                parts.append(f"[{inner}]({href})")
            else:
                parts.append(inner)
        elif tag == "br":
            parts.append("\n")
        elif tag == "img":
            alt = (child.attributes.get("alt") or "").strip() if child.attributes else ""
            src = (child.attributes.get("src") or "").strip() if child.attributes else ""
            if src:
                parts.append(f"![{alt}]({src})")
        else:
            parts.append(_inline(child))
    return _collapse_ws(" ".join(p for p in parts if p))


def _render_table(node: Node) -> str:
    rows: list[list[str]] = []
    for tr in node.css("tr"):
        cells = [_inline(c) for c in tr.css("th, td")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []
    sep = ["---"] * width
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _collapse_ws(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def _post_process(md: str) -> str:
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"
