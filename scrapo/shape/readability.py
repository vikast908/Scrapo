"""Main-content (readability-style) extraction.

Given a full HTML page, :func:`extract_main_content` tries to isolate the
article body and strip boilerplate (navigation, sidebars, footers, comment
threads, ads, share/social widgets, breadcrumbs, cookie banners, ...). The
result is an HTML fragment intended to be fed straight into
:func:`scrapo.shape.markdown.to_markdown` so the downstream Markdown is cleaner.

Approach (Readability / Trafilatura inspired, selectolax only):

1. Parse the document once with ``selectolax.parser.HTMLParser``.
2. **Strong semantic signals first.** If the page exposes ``<article>``,
   ``<main>`` or an element with ``role="main"``, prefer the largest such node
   by text length -- authors who use these tags almost always wrap the real
   content in them.
3. **Otherwise, score candidate block containers** (``div``, ``section``,
   ``article``, ``main``, ``td``). Each candidate's score is roughly::

       score = text_length
             - link_density_penalty   (text inside <a> / total text)
             - boilerplate_penalty    (nav|menu|sidebar|footer|... in id/class)
             + paragraph_reward       (count of <p> with substantive text)
             + content_name_reward    (content|article|post|entry|... in id/class)

   Text length per candidate is computed exactly once (no O(n^2) re-walks).
4. Pick the highest-scoring node and return its outer ``.html``. If the best
   score is non-positive, or the chosen node is tiny relative to the whole page,
   we treat the result as untrustworthy.

**Fallback contract:** this function NEVER raises and, whenever it cannot
confidently identify a main region (no good candidate, parse error, empty /
malformed input, ...), it returns the original ``html`` *unchanged*. Callers can
therefore always pass the return value on to the Markdown converter safely.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser, Node

# Container tags we are willing to treat as the main-content root.
_CANDIDATE_TAGS = frozenset({"div", "section", "article", "main", "td"})

# id/class substrings that strongly suggest boilerplate -> heavy penalty.
_NEGATIVE_RE = re.compile(
    r"nav|menu|sidebar|footer|header|comment|share|social|related|promo"
    r"|\bads?\b|advert|breadcrumb|cookie|banner|popup|masthead|widget"
    r"|sponsor|subscribe|newsletter|pagination",
    re.I,
)

# id/class substrings that suggest real content -> reward.
_POSITIVE_RE = re.compile(
    r"content|article|post|entry|main|story|body|text|blog|page",
    re.I,
)

# A paragraph counts as "substantive" once it has at least this many chars.
_MIN_PARAGRAPH_CHARS = 25

# Weights for the heuristic. Tuned so a real article body beats both link
# menus (high link density) and boilerplate-named blocks.
_LINK_DENSITY_WEIGHT = 0.8
_NEGATIVE_PENALTY = 50.0
_POSITIVE_REWARD = 25.0
_PARAGRAPH_REWARD = 30.0

# The winner must contain at least this fraction of the page's total text,
# otherwise we don't trust it and fall back to the original html.
_MIN_TEXT_FRACTION = 0.10
# ... unless it has this many absolute characters (a real article on a huge
# page can fall below the fraction yet still be the genuine content).
_MIN_ABSOLUTE_CHARS = 200


def extract_main_content(html: str) -> str:
    """Return an HTML fragment containing just the main content of ``html``.

    Falls back to returning ``html`` unchanged when no confident main region is
    found. Never raises.
    """
    if not html or not html.strip():
        return html
    try:
        return _extract(html)
    except Exception:
        # Any selectolax / parsing edge case: degrade gracefully.
        return html


def _extract(html: str) -> str:
    tree = HTMLParser(html)
    body = tree.css_first("body") or tree.root
    if body is None:
        return html

    total_text = _text_len(body)
    if total_text == 0:
        return html

    # 1. Strong semantic signals: <article>, <main>, [role=main].
    semantic = _best_semantic_node(tree)
    if semantic is not None:
        node, node_text = semantic
        if _is_trustworthy(node_text, total_text):
            out = node.html
            if out:
                return out

    # 2. Heuristic scoring over candidate containers.
    best_node: Node | None = None
    best_score = 0.0
    best_text = 0

    for node in body.iter():
        scored = _score_candidate(node)
        if scored is None:
            continue
        score, node_text = scored
        if score > best_score:
            best_score = score
            best_node = node
            best_text = node_text

    if best_node is not None and best_score > 0 and _is_trustworthy(best_text, total_text):
        out = best_node.html
        if out:
            return out

    # 3. Nothing trustworthy -> fall back.
    return html


def _best_semantic_node(tree: HTMLParser) -> tuple[Node, int] | None:
    """Largest (by text length) of <article>, <main>, or [role=main]."""
    best: Node | None = None
    best_len = 0
    for node in tree.css("article, main, [role=main]"):
        node_text = _text_len(node)
        if node_text > best_len:
            best_len = node_text
            best = node
    if best is None or best_len == 0:
        return None
    return best, best_len


def _score_candidate(node: Node) -> tuple[float, int] | None:
    """Score a single candidate container, or ``None`` if it isn't one."""
    if node.tag not in _CANDIDATE_TAGS:
        return None

    node_text = _text_len(node)
    if node_text == 0:
        return None

    score = float(node_text)

    # Penalise link density: a menu / link list is mostly anchor text.
    link_text = _link_text_len(node)
    link_density = link_text / node_text
    score -= link_density * node_text * _LINK_DENSITY_WEIGHT

    # Reward substantive paragraphs.
    score += _paragraph_count(node) * _PARAGRAPH_REWARD

    # id/class based signals.
    ident = _ident(node)
    if ident:
        if _NEGATIVE_RE.search(ident):
            score -= _NEGATIVE_PENALTY
        if _POSITIVE_RE.search(ident):
            score += _POSITIVE_REWARD

    return score, node_text


def _is_trustworthy(node_text: int, total_text: int) -> bool:
    if node_text >= _MIN_ABSOLUTE_CHARS:
        return True
    return node_text >= total_text * _MIN_TEXT_FRACTION


def _ident(node: Node) -> str:
    attrs = node.attributes
    if not attrs:
        return ""
    parts = [attrs.get("id") or "", attrs.get("class") or ""]
    return " ".join(p for p in parts if p)


def _text_len(node: Node) -> int:
    text = node.text(deep=True, strip=True)
    return len(text) if text else 0


def _link_text_len(node: Node) -> int:
    total = 0
    for a in node.css("a"):
        text = a.text(deep=True, strip=True)
        if text:
            total += len(text)
    return total


def _paragraph_count(node: Node) -> int:
    count = 0
    for p in node.css("p"):
        text = p.text(deep=True, strip=True)
        if text and len(text) >= _MIN_PARAGRAPH_CHARS:
            count += 1
    return count
