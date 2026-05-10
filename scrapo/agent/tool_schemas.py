"""Tool schemas in the Anthropic / OpenAI / generic JSON-schema shape.

These are loaded by the MCP server and can also be passed verbatim to
Anthropic.messages.create(tools=...) or OpenAI's tool param.
"""

from __future__ import annotations

from typing import Any

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "scrapo_scrape",
        "description": "Fetch a single URL through Scrapo's tier router and return clean "
        "markdown + provenance-tagged chunks. Optionally extracts typed JSON. Re-scraping a "
        "URL uses a conditional GET, so an unchanged page comes back fast (not_modified=true).",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to scrape"},
                "wait_for": {
                    "type": "string",
                    "description": "CSS selector to wait for (browser tier only)",
                },
                "screenshot": {"type": "boolean", "default": False},
                "max_tier": {
                    "type": "integer",
                    "description": "Cap escalation. 0=HTTP 1=HTTP+session 2=browser 3=stealth 4=agent",
                    "default": 2,
                },
                "diff_last": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also return a field-level diff against the previous recorded run of this URL",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "scrapo_crawl",
        "description": "Recursive crawl from one or more seed URLs. Returns crawl_id and stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seeds": {"type": "array", "items": {"type": "string"}},
                "max_depth": {"type": "integer", "default": 2},
                "max_pages": {"type": "integer", "default": 100},
                "same_host_only": {"type": "boolean", "default": True},
            },
            "required": ["seeds"],
        },
    },
    {
        "name": "scrapo_replay",
        "description": "Re-run extraction over a previously archived run's HTML "
        "without re-fetching the live page.",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "scrapo_diff",
        "description": "Field-level diff between two recorded runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_a": {"type": "string"},
                "run_b": {"type": "string"},
            },
            "required": ["run_a", "run_b"],
        },
    },
    {
        "name": "scrapo_list_runs",
        "description": "List recent recorded runs, optionally filtered by URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
]
