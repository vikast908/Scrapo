"""Zero-LLM extraction from embedded structured data.

A large fraction of commercial pages (products, articles, recipes, jobs, events)
already hand you their structured fields in the markup: a
``<script type="application/ld+json">`` schema.org block, Open Graph / Twitter
``<meta>`` tags, or microdata ``itemprop`` attributes. When the target Pydantic
schema can be satisfied straight from that data, there is no reason to spend a
selector-cache lookup — let alone an LLM call — deriving it.

This module is the cheapest rung of the extraction ladder, tried *before* the
selector cache in :class:`scrapo.extract.hybrid.HybridExtractor`:

    embedded metadata  ->  selector cache  ->  LLM

It is deliberately conservative. It only returns a result when

  * every *required* schema field could be sourced from structured data, and
  * the assembled object validates against the model.

Otherwise it returns ``None`` and the extractor falls through to the existing
selector / LLM path, so turning it on never costs correctness — at worst it does
a little parsing and gives up. The bare ``<title>`` element is intentionally NOT
treated as a source: it is generic page chrome, not a structured annotation, and
using it would let metadata hijack fields it shouldn't.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, get_origin

from pydantic import BaseModel, ValidationError
from selectolax.parser import HTMLParser

from scrapo.extract.schema import list_fields as _model_list_fields
from scrapo.extract.schema import required_fields

# Open Graph / Twitter / OGP-vertical meta prefixes that we strip before matching
# (``og:title`` and ``article:published_time`` both describe a field named after
# their suffix).
_META_PREFIXES = ("og:", "twitter:", "article:", "product:", "book:", "music:", "video:")

# Scalar keys that mean the same thing across JSON-LD (camelCase), Open Graph,
# and microdata. Each member maps to the whole group so a schema field named any
# one of them resolves from any source key in the group. Everything is normalised
# (lowercase, alphanumerics only) before lookup.
_ALIAS_GROUPS_RAW: tuple[tuple[str, ...], ...] = (
    ("title", "name", "headline"),
    ("description", "summary", "abstract"),
    ("image", "imageurl", "thumbnail", "thumbnailurl", "primaryimageofpage"),
    ("price", "lowprice", "highprice"),
    ("currency", "pricecurrency"),
    ("author", "creator", "byline"),
    ("datepublished", "publishedtime", "pubdate", "datecreated", "published", "date"),
    ("datemodified", "modifiedtime", "updated", "lastmodified"),
    ("url", "canonical", "permalink"),
    ("rating", "ratingvalue"),
    ("sku", "productid", "mpn"),
    ("brand", "manufacturer"),
)


def _build_alias_map() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for group in _ALIAS_GROUPS_RAW:
        members = set(group)
        for member in group:
            out[member] = members
    return out


_ALIASES = _build_alias_map()

# Keys whose nested-object value we collapse to a representative scalar (so
# ``author: {"@type": "Person", "name": "Ada"}`` resolves to ``"Ada"`` and
# ``offers: {"price": "42"}`` exposes a ``price``).
_REPRESENTATIVE_KEYS = ("name", "title", "@value", "value", "text", "url", "headline")

_MAX_FLATTEN_DEPTH = 3
_MAX_JSONLD_BYTES = 512_000  # ignore a pathologically large JSON-LD blob


@dataclass(slots=True)
class MetadataResult:
    """A schema satisfied entirely from embedded structured data."""

    data: BaseModel
    source: str  # "json-ld" | "opengraph" | "microdata"
    fields: dict[str, str]  # field name -> source label, for provenance


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _strip_meta_prefix(key: str) -> str:
    low = key.lower()
    for pre in _META_PREFIXES:
        if low.startswith(pre):
            return low[len(pre) :]
    return low


def _candidates(field_name: str) -> set[str]:
    norm = _norm(field_name)
    return {norm} | _ALIASES.get(norm, set())


def _representative(obj: Any) -> Any:
    """Collapse a dict/list to a single scalar for a field that wants a scalar."""
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return _representative(obj[0]) if obj else None
    if isinstance(obj, dict):
        for key in _REPRESENTATIVE_KEYS:
            if key in obj:
                rep = _representative(obj[key])
                if rep is not None:
                    return rep
    return None


def _flatten(obj: dict[str, Any], out: dict[str, Any], depth: int = 0) -> None:
    """Add scalar leaves of ``obj`` to ``out`` keyed by their normalised leaf name.

    Nested objects contribute both a representative scalar under their own key and
    (recursively) their children, so ``offers.price`` becomes a resolvable
    ``price``. ``setdefault`` keeps the first (highest-precedence) writer.
    """
    if depth > _MAX_FLATTEN_DEPTH:
        return
    for key, value in obj.items():
        if key.startswith("@"):
            continue
        nk = _norm(key)
        if isinstance(value, (str, int, float, bool)):
            out.setdefault(nk, value)
        elif isinstance(value, dict):
            rep = _representative(value)
            if rep is not None:
                out.setdefault(nk, rep)
            _flatten(value, out, depth + 1)
        elif isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, (str, int, float, bool)):
                out.setdefault(nk, first)
            elif isinstance(first, dict):
                rep = _representative(first)
                if rep is not None:
                    out.setdefault(nk, rep)
                _flatten(first, out, depth + 1)


def _collect_jsonld(tree: HTMLParser) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        blob = node.text() or ""
        if not blob.strip() or len(blob) > _MAX_JSONLD_BYTES:
            continue
        try:
            parsed = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue
        _absorb_jsonld(parsed, objects)
    return objects


def _absorb_jsonld(parsed: Any, objects: list[dict[str, Any]]) -> None:
    if isinstance(parsed, list):
        for item in parsed:
            _absorb_jsonld(item, objects)
    elif isinstance(parsed, dict):
        graph = parsed.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                _absorb_jsonld(item, objects)
        # Keep the object itself too (it may carry top-level fields alongside @graph).
        objects.append(parsed)


def _collect_meta(tree: HTMLParser) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for node in tree.css("meta"):
        attrs = node.attributes
        content = attrs.get("content")
        if not content:
            continue
        raw_key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        if not raw_key:
            continue
        out.setdefault(_norm(_strip_meta_prefix(raw_key)), content)
    return out


def _collect_microdata(tree: HTMLParser) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for node in tree.css("[itemprop]"):
        prop = node.attributes.get("itemprop")
        if not prop:
            continue
        # content= / value= win over the visible text (the schema.org convention).
        value = node.attributes.get("content") or node.attributes.get("value")
        if not value:
            value = node.text(strip=True)
        if value:
            out.setdefault(_norm(prop), value)
    return out


def _collect_time(tree: HTMLParser) -> dict[str, Any]:
    """Pull a publication date from HTML5 ``<time datetime=...>`` elements.

    A huge fraction of articles mark their date only with a ``<time>`` tag rather
    than JSON-LD/OpenGraph, so reading it closes most of the date-coverage gap.
    Prefers a ``<time>`` whose itemprop/class/pubdate marks it as a *published*
    date; otherwise falls back to the first ``<time datetime>``. Modified/updated
    timestamps are skipped so they don't shadow the publish date. Mapped to
    ``datepublished`` so a schema's date field resolves from it.
    """
    out: dict[str, Any] = {}
    fallback: str | None = None
    for node in tree.css("time"):
        attrs = node.attributes
        dt = (attrs.get("datetime") or "").strip()
        if not dt:
            continue
        ident = " ".join(
            filter(
                None,
                (
                    attrs.get("itemprop") or "",
                    attrs.get("class") or "",
                    "pubdate" if "pubdate" in attrs else "",
                ),
            )
        ).lower()
        if any(k in ident for k in ("publish", "posted", "pubdate", "created")) and not any(
            k in ident for k in ("modif", "updat")
        ):
            out.setdefault("datepublished", dt)
        elif fallback is None and not any(k in ident for k in ("modif", "updat")):
            fallback = dt
    if "datepublished" not in out and fallback is not None:
        out["datepublished"] = fallback
    return out


def _list_field_models(model: type[BaseModel]) -> dict[str, type[BaseModel] | None]:
    """field name -> item model (``None`` for ``list[str]`` and friends)."""
    out: dict[str, type[BaseModel] | None] = {}
    nested = _model_list_fields(model)  # list[BaseModel] fields only
    for name, field in model.model_fields.items():
        ann = field.annotation
        origin = get_origin(ann)
        if origin in (list, set, tuple, frozenset):
            out[name] = nested.get(name)
    return out


def _resolve_scalar(field_name: str, source: dict[str, Any]) -> Any:
    for cand in _candidates(field_name):
        if cand in source:
            val = source[cand]
            return _representative(val) if isinstance(val, (dict, list)) else val
    return None


def _find_list(field_name: str, ld_objects: list[dict[str, Any]]) -> list[Any] | None:
    cands = _candidates(field_name)
    for obj in ld_objects:
        for key, value in obj.items():
            if _norm(key) in cands and isinstance(value, list) and value:
                return value
        # schema.org ItemList: a generic list field maps to itemListElement[*].item
        elements = obj.get("itemListElement")
        if isinstance(elements, list) and elements:
            unwrapped = [e.get("item", e) if isinstance(e, dict) else e for e in elements]
            if unwrapped:
                return unwrapped
    return None


def _map_list(raw: list[Any], item_model: type[BaseModel] | None) -> list[Any]:
    rows: list[Any] = []
    for el in raw:
        if item_model is None:
            rows.append(_representative(el) if isinstance(el, (dict, list)) else el)
            continue
        if not isinstance(el, dict):
            continue
        flat: dict[str, Any] = {}
        _flatten(el, flat)
        row: dict[str, Any] = {}
        for sub_name in item_model.model_fields:
            val = _resolve_scalar(sub_name, flat)
            if val is not None:
                row[sub_name] = val
        if row:
            rows.append(row)
    return rows


def extract_from_metadata(html: str, model: type[BaseModel]) -> MetadataResult | None:
    """Try to satisfy ``model`` from embedded structured data alone.

    Returns a :class:`MetadataResult` only when every required field was found and
    the object validates; otherwise ``None`` (the caller falls back to selectors /
    LLM). Never raises — malformed markup just yields ``None``.
    """
    if not html:
        return None
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001 - unparseable markup → no metadata, fall through
        return None

    ld_objects = _collect_jsonld(tree)
    meta = _collect_meta(tree)
    micro = _collect_microdata(tree)
    times = _collect_time(tree)
    if not ld_objects and not meta and not micro and not times:
        return None

    # Precedence: JSON-LD (richest) > microdata > meta > <time> tag (a date-only
    # fallback). setdefault keeps the first writer, so flatten JSON-LD first.
    scalar_source: dict[str, Any] = {}
    for obj in ld_objects:
        _flatten(obj, scalar_source)
    for key, value in micro.items():
        scalar_source.setdefault(key, value)
    for key, value in meta.items():
        scalar_source.setdefault(key, value)
    for key, value in times.items():
        scalar_source.setdefault(key, value)

    list_models = _list_field_models(model)
    resolved: dict[str, Any] = {}
    for field_name in model.model_fields:
        if field_name in list_models:
            raw = _find_list(field_name, ld_objects)
            if raw is not None:
                mapped = _map_list(raw, list_models[field_name])
                if mapped:
                    resolved[field_name] = mapped
            continue
        val = _resolve_scalar(field_name, scalar_source)
        if val is not None:
            resolved[field_name] = val

    if not resolved:
        return None
    required = set(required_fields(model))
    if required and not required.issubset(resolved.keys()):
        return None

    try:
        data = model.model_validate(resolved)
    except ValidationError:
        return None

    source = "json-ld" if ld_objects else ("microdata" if micro else "opengraph")
    return MetadataResult(
        data=data, source=source, fields={name: source for name in resolved}
    )
