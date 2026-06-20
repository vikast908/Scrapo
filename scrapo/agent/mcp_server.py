"""MCP server exposing Scrapo's core capabilities as MCP tools.

Run with: `python -m scrapo.agent.mcp_server` (stdio transport).
"""

from __future__ import annotations

import json
from typing import Any

from scrapo.api import batch_scrape, crawl, map_site, scrape
from scrapo.config import get_config
from scrapo.replay.diff import diff_runs, diff_summary
from scrapo.replay.store import ReplayStore
from scrapo.shape.provenance import shape_document
from scrapo.types import Budget, Tier


async def _scrapo_scrape(args: dict[str, Any]) -> dict[str, Any]:
    max_tier = Tier(int(args.get("max_tier", 2)))
    budget = Budget(max_tier=max_tier)
    api_first = args.get("api_first")
    result = await scrape(
        args["url"],
        budget=budget,
        wait_for=args.get("wait_for"),
        screenshot=bool(args.get("screenshot", False)),
        api_first=None if api_first is None else bool(api_first),
    )
    payload = result.model_dump(mode="json")
    if args.get("diff_last") and not result.blocked:
        store = ReplayStore(get_config())
        runs = await store.list_runs(url=args["url"], limit=2)
        if len(runs) >= 2:
            report = await diff_runs(store, runs[1]["run_id"], runs[0]["run_id"])
            payload["diff"] = {"summary": diff_summary(report), **report.to_dict()}
    return payload


_MCP_MAX_SEEDS = 20
_MCP_MAX_DEPTH = 5
_MCP_MAX_PAGES = 1000


async def _scrapo_crawl(args: dict[str, Any]) -> dict[str, Any]:
    seeds = args.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        return {"error": "seeds must be a non-empty list of URLs"}
    if len(seeds) > _MCP_MAX_SEEDS:
        return {"error": f"too many seeds: {len(seeds)} > {_MCP_MAX_SEEDS}"}
    if not all(isinstance(s, str) for s in seeds):
        return {"error": "every seed must be a string URL"}
    max_pages = _clamp_int(args.get("max_pages", 100), low=1, high=_MCP_MAX_PAGES)
    max_depth = _clamp_int(args.get("max_depth", 2), low=0, high=_MCP_MAX_DEPTH)
    budget = Budget(max_tier=Tier.BROWSER, max_pages=max_pages)
    result = await crawl(
        seeds,
        budget=budget,
        max_depth=max_depth,
        same_host_only=bool(args.get("same_host_only", True)),
    )
    return result.model_dump(mode="json")


_MCP_MAX_MAP_URLS = 5000
_MCP_MAX_BATCH = 100


async def _scrapo_map(args: dict[str, Any]) -> dict[str, Any]:
    seeds = args.get("seeds")
    if not isinstance(seeds, list) or not seeds or not all(isinstance(s, str) for s in seeds):
        return {"error": "seeds must be a non-empty list of URL strings"}
    if len(seeds) > _MCP_MAX_SEEDS:
        return {"error": f"too many seeds: {len(seeds)} > {_MCP_MAX_SEEDS}"}
    max_urls = _clamp_int(args.get("max_urls", 1000), low=1, high=_MCP_MAX_MAP_URLS)
    max_depth = _clamp_int(args.get("max_depth", 2), low=0, high=_MCP_MAX_DEPTH)
    urls = await map_site(
        seeds,
        max_urls=max_urls,
        max_depth=max_depth,
        same_host_only=bool(args.get("same_host_only", True)),
        use_sitemap=bool(args.get("use_sitemap", True)),
    )
    return {"count": len(urls), "urls": urls}


async def _scrapo_batch(args: dict[str, Any]) -> dict[str, Any]:
    urls = args.get("urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
        return {"error": "urls must be a non-empty list of URL strings"}
    if len(urls) > _MCP_MAX_BATCH:
        return {"error": f"too many urls: {len(urls)} > {_MCP_MAX_BATCH}"}
    max_tier = Tier(_clamp_int(args.get("max_tier", 2), low=0, high=4))
    items = await batch_scrape(
        urls,
        budget=Budget(max_tier=max_tier),
        main_content=bool(args.get("main_content", False)),
    )
    return {
        "results": [
            {
                "url": it.url,
                "error": it.error,
                "result": it.result.model_dump(mode="json") if it.result is not None else None,
            }
            for it in items
        ]
    }


def _clamp_int(value: Any, *, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, n))


async def _scrapo_replay(args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    store = ReplayStore(cfg)
    record = await store.get(args["run_id"])
    if record is None:
        return {"error": "run_id not found"}
    html = await store.load_html(args["run_id"])
    if html is None:
        return {"error": "no archived html for run"}
    document = shape_document(html, record["url"])
    return {
        "run_id": record["run_id"],
        "url": record["url"],
        "title": document.title,
        "markdown": document.markdown[:8000],
        "extraction": json.loads(record["extraction_json"]) if record.get("extraction_json") else None,
    }


async def _scrapo_diff(args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    store = ReplayStore(cfg)
    report = await diff_runs(store, args["run_a"], args["run_b"])
    return {"summary": diff_summary(report), **report.to_dict()}


async def _scrapo_list_runs(args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    store = ReplayStore(cfg)
    runs = await store.list_runs(url=args.get("url"), limit=int(args.get("limit", 20)))
    return {"runs": runs}


_HANDLERS = {
    "scrapo_scrape": _scrapo_scrape,
    "scrapo_crawl": _scrapo_crawl,
    "scrapo_map": _scrapo_map,
    "scrapo_batch": _scrapo_batch,
    "scrapo_replay": _scrapo_replay,
    "scrapo_diff": _scrapo_diff,
    "scrapo_list_runs": _scrapo_list_runs,
}

# Cap on the serialized response size. MCP payloads should stay reasonably
# small; oversized results are truncated to *valid* JSON rather than sliced
# mid-string (which would corrupt the payload).
_MCP_MAX_BYTES = 200_000

# Fields that can balloon a single result; trimmed first when shrinking.
_LARGE_TEXT_FIELDS = ("html", "markdown", "raw_content", "text", "content", "screenshot")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def _trim_large_fields(obj: Any) -> bool:
    """Best-effort, in-place trim of known large string fields. Returns True if
    anything was changed."""
    changed = False
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if key in _LARGE_TEXT_FIELDS and isinstance(value, str) and len(value) > 2_000:
                obj[key] = value[:2_000] + f"... [truncated, {len(value)} chars total]"
                changed = True
            elif _trim_large_fields(value):
                changed = True
    elif isinstance(obj, list):
        for item in obj:
            if _trim_large_fields(item):
                changed = True
    return changed


def _serialize_result(result: Any) -> str:
    """Serialize a handler result to VALID JSON, never exceeding the size cap.

    Strategy: serialize as-is; if too large, trim known large fields and flag
    truncation; if still too large, fall back to a structured size-error notice.
    Every return value is valid JSON.
    """
    encoded = _dumps(result)
    if len(encoded.encode("utf-8")) <= _MCP_MAX_BYTES:
        return encoded

    original_bytes = len(encoded.encode("utf-8"))
    if isinstance(result, dict):
        trimmed = dict(result)
    elif isinstance(result, list):
        trimmed = {"items": list(result)}
    else:
        trimmed = {"result": result}

    if _trim_large_fields(trimmed):
        trimmed["truncated"] = True
        encoded = _dumps(trimmed)
        if len(encoded.encode("utf-8")) <= _MCP_MAX_BYTES:
            return encoded

    return _dumps(
        {
            "error": "result too large",
            "bytes": original_bytes,
            "truncated": True,
            "hint": "narrow the query / lower max_pages",
        }
    )


async def serve_stdio() -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError as e:
        raise SystemExit(
            "Install scrapo[mcp] to run the MCP server"
        ) from e

    from scrapo.agent.tool_schemas import TOOL_SCHEMAS

    server: Server = Server("scrapo")

    @server.list_tools()  # type: ignore[untyped-decorator, no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=t["name"], description=t["description"], inputSchema=t["input_schema"])
            for t in TOOL_SCHEMAS
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handler = _HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=_dumps({"error": f"unknown tool: {name}"}))]
        try:
            result = await handler(arguments or {})
        except Exception as e:  # noqa: BLE001 - surface any handler failure as a clean payload
            return [
                TextContent(
                    type="text",
                    text=_dumps({"error": str(e), "type": e.__class__.__name__}),
                )
            ]
        return [TextContent(type="text", text=_serialize_result(result))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio

    from scrapo.logging import configure_logging

    configure_logging()
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()
