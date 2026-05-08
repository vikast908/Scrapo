"""Pydantic schema utilities — JSON-schema export, stable hashing, version derivation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


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
