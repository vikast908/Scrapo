"""MCP server exposing Scrapo's core capabilities as MCP tools.

Run with: `python -m scrapo.agent.mcp_server` (stdio transport).
"""

from __future__ import annotations

import json
from typing import Any

from scrapo.api import crawl, scrape
from scrapo.config import get_config
from scrapo.replay.diff import diff_runs, diff_summary
from scrapo.replay.store import ReplayStore
from scrapo.shape.provenance import shape_document
from scrapo.types import Budget, Tier


async def _scrapo_scrape(args: dict[str, Any]) -> dict[str, Any]:
    max_tier = Tier(int(args.get("max_tier", 2)))
    budget = Budget(max_tier=max_tier)
    result = await scrape(
        args["url"],
        budget=budget,
        wait_for=args.get("wait_for"),
        screenshot=bool(args.get("screenshot", False)),
    )
    return result.model_dump(mode="json")


async def _scrapo_crawl(args: dict[str, Any]) -> dict[str, Any]:
    budget = Budget(max_tier=Tier.BROWSER, max_pages=int(args.get("max_pages", 100)))
    result = await crawl(
        args["seeds"],
        budget=budget,
        max_depth=int(args.get("max_depth", 2)),
        same_host_only=bool(args.get("same_host_only", True)),
    )
    return result.model_dump(mode="json")


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
    "scrapo_replay": _scrapo_replay,
    "scrapo_diff": _scrapo_diff,
    "scrapo_list_runs": _scrapo_list_runs,
}


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

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(name=t["name"], description=t["description"], inputSchema=t["input_schema"])
            for t in TOOL_SCHEMAS
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handler = _HANDLERS.get(name)
        if handler is None:
            return [TextContent(type="text", text=json.dumps({"error": f"unknown tool: {name}"}))]
        result = await handler(arguments or {})
        return [TextContent(type="text", text=json.dumps(result, default=str)[:200_000])]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio

    from scrapo.logging import configure_logging

    configure_logging()
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()
