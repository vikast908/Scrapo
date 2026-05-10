"""Pydantic schema utilities: JSON-schema export, stable hashing, version derivation."""

from __future__ import annotations

import hashlib
import json
import types
import typing
from typing import Any, get_args, get_origin

from pydantic import BaseModel

_UNION_ORIGINS = (typing.Union, types.UnionType)


def schema_to_jsonschema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def schema_hash(model: type[BaseModel]) -> str:
    """Stable hash of a Pydantic model — used as cache key for selectors."""
    payload = json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def schema_version(model: type[BaseModel]) -> str:
    """Human-readable version: ClassName@hash. Pinned alongside extraction output."""
    return f"{model.__name__}@{schema_hash(model)}"


def required_fields(model: type[BaseModel]) -> list[str]:
    js = model.model_json_schema()
    return list(js.get("required", []))


def _unwrap_optional(tp: Any) -> Any:
    """If ``tp`` is ``Optional[X]`` / ``X | None``, return ``X``; else return ``tp``."""
    if get_origin(tp) in _UNION_ORIGINS:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return tp


def list_fields(model: type[BaseModel]) -> dict[str, type[BaseModel]]:
    """Map field name -> item model for fields typed ``list[SomeBaseModel]``.

    These get the repeated-element extraction path: one container selector plus
    per-subfield selectors relative to it.
    """
    out: dict[str, type[BaseModel]] = {}
    for name, field in model.model_fields.items():
        ann = _unwrap_optional(field.annotation)
        if get_origin(ann) is list:
            args = get_args(ann)
            if args:
                item_tp = _unwrap_optional(args[0])
                if isinstance(item_tp, type) and issubclass(item_tp, BaseModel):
                    out[name] = item_tp
    return out
