"""Scrapo CLI — `scrapo scrape`, `scrapo crawl`, `scrapo replay`, `scrapo diff`, etc."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from scrapo.api import batch_scrape as batch_api
from scrapo.api import crawl as crawl_api
from scrapo.api import map_site as map_api
from scrapo.api import scrape as scrape_api
from scrapo.config import Config, set_config
from scrapo.logging import configure_logging
from scrapo.replay.diff import diff_runs, diff_summary
from scrapo.replay.store import ReplayStore
from scrapo.shape.provenance import shape_document
from scrapo.types import Budget, Tier

app = typer.Typer(no_args_is_help=True, help="Scrapo: AI-native, agent-first scraping")
console = Console()

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _load_config(data_dir: Path | None) -> Config:
    cfg = Config.from_env()
    if data_dir:
        cfg.data_dir = Path(data_dir).expanduser()
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
    set_config(cfg)
    return cfg


@app.command()
def scrape(
    url: Annotated[str, typer.Argument(help="URL to fetch")],
    max_tier: Annotated[int, typer.Option(help="0=HTTP 1=HTTP+session 2=browser 3=stealth 4=agent")] = 2,
    wait_for: Annotated[str | None, typer.Option(help="CSS selector to wait for (browser tier)")] = None,
    screenshot: Annotated[bool, typer.Option(help="Capture screenshot (browser tier)")] = False,
    main_content: Annotated[bool, typer.Option(help="Strip boilerplate (nav/sidebar/footer) before markdown")] = False,
    diff_last: Annotated[bool, typer.Option(help="Diff against the previous recorded run of this URL")] = False,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
    out_md: Annotated[Path | None, typer.Option(help="Write markdown to this file")] = None,
    out_json: Annotated[Path | None, typer.Option(help="Write full JSON to this file")] = None,
) -> None:
    """Fetch one URL and print clean markdown."""
    cfg = _load_config(data_dir)
    budget = Budget(max_tier=Tier(max_tier))
    result = asyncio.run(
        scrape_api(
            url, budget=budget, wait_for=wait_for, screenshot=screenshot,
            main_content=main_content,
        )
    )
    if result.get("blocked"):
        console.print(f"[red]blocked:[/red] {result.get('block_reason')}")
        raise typer.Exit(code=2)
    flags = " [yellow]not-modified[/yellow]" if result.get("not_modified") else ""
    console.print(
        f"[green]OK[/green] {result['url']}{flags}  "
        f"[dim]tier={result['tier_used']} status={result['status']} "
        f"chunks={len(result['chunks'])} run={result['run_id'][:12]}...[/dim]"
    )
    if diff_last:
        store = ReplayStore(cfg)
        runs = asyncio.run(store.list_runs(url=url, limit=2))
        if len(runs) >= 2:
            report = asyncio.run(diff_runs(store, runs[1]["run_id"], runs[0]["run_id"]))
            console.print(diff_summary(report))
        else:
            console.print("[dim]no prior run to diff against[/dim]")
    if out_md:
        out_md.write_text(result["markdown"] or "", encoding="utf-8")
    if out_json:
        out_json.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    if not out_md and not out_json:
        console.print((result["markdown"] or "")[:4000])


@app.command()
def crawl(
    seed: Annotated[list[str], typer.Argument(help="One or more seed URLs")],
    max_depth: Annotated[int, typer.Option(help="Recursion depth")] = 2,
    max_pages: Annotated[int, typer.Option(help="Page budget")] = 50,
    max_tier: Annotated[int, typer.Option(help="Tier ceiling")] = 2,
    same_host: Annotated[bool, typer.Option(help="Restrict to seed hosts")] = True,
    use_sitemap: Annotated[bool, typer.Option(help="Also seed from each origin's sitemap.xml")] = False,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Crawl from seed URLs."""
    _load_config(data_dir)
    budget = Budget(max_tier=Tier(max_tier), max_pages=max_pages)
    result = asyncio.run(
        crawl_api(
            seed, budget=budget, max_depth=max_depth, same_host_only=same_host, use_sitemap=use_sitemap
        )
    )
    console.print(f"[green]crawl_id:[/green] {result['crawl_id']}")
    table = Table("status", "count")
    for status, count in result["stats"].items():
        table.add_row(status, str(count))
    console.print(table)


@app.command(name="map")
def map_cmd(
    seed: Annotated[list[str], typer.Argument(help="One or more seed URLs")],
    max_depth: Annotated[int, typer.Option(help="Link-discovery depth")] = 2,
    max_urls: Annotated[int, typer.Option(help="Cap on discovered URLs")] = 5000,
    same_host: Annotated[bool, typer.Option(help="Restrict to seed hosts")] = True,
    use_sitemap: Annotated[bool, typer.Option(help="Merge each origin's sitemap.xml")] = True,
    out: Annotated[Path | None, typer.Option(help="Write the URL list to this file")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Discover a site's URLs without scraping their content."""
    _load_config(data_dir)
    urls = asyncio.run(
        map_api(
            seed, max_depth=max_depth, max_urls=max_urls,
            same_host_only=same_host, use_sitemap=use_sitemap,
        )
    )
    console.print(f"[green]{len(urls)} URLs[/green]")
    if out:
        out.write_text("\n".join(urls), encoding="utf-8")
    else:
        for u in urls:
            console.print(u)


@app.command()
def batch(
    url: Annotated[list[str], typer.Argument(help="URLs to scrape")],
    max_tier: Annotated[int, typer.Option(help="Tier ceiling")] = 2,
    concurrency: Annotated[int | None, typer.Option(help="Max concurrent scrapes")] = None,
    main_content: Annotated[bool, typer.Option(help="Strip boilerplate before markdown")] = False,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Scrape an explicit list of URLs concurrently (not a recursive crawl)."""
    _load_config(data_dir)
    budget = Budget(max_tier=Tier(max_tier))
    items = asyncio.run(
        batch_api(url, budget=budget, max_concurrency=concurrency, main_content=main_content)
    )
    table = Table("url", "status", "tier", "chunks", "error")
    for it in items:
        r = it.result
        table.add_row(
            it.url[:50],
            str(r["status"]) if r else "-",
            (r["tier_used"] or "-") if r else "-",
            str(len(r["chunks"])) if r else "-",
            (it.error or ("blocked" if r and r.get("blocked") else ""))[:30],
        )
    console.print(table)
    ok = sum(1 for it in items if it.result and not it.result.get("blocked"))
    console.print(f"[green]{ok}/{len(items)} ok[/green]")


@app.command(name="list")
def list_runs(
    url: Annotated[str | None, typer.Option(help="Filter by URL")] = None,
    limit: Annotated[int, typer.Option()] = 20,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """List recent runs."""
    cfg = _load_config(data_dir)
    runs = asyncio.run(ReplayStore(cfg).list_runs(url=url, limit=limit))
    if not runs:
        console.print("[dim]no runs yet[/dim]")
        return
    table = Table("run_id", "url", "tier", "status", "method", "model")
    for r in runs:
        table.add_row(
            r["run_id"][:12],
            r["url"][:60],
            str(Tier(r["tier_used"]).label) if r["tier_used"] is not None else "-",
            str(r["fetch_status"] or "-"),
            r["extraction_method"] or "-",
            r["model_pinned"] or "-",
        )
    console.print(table)


@app.command()
def replay(
    run_id: Annotated[str, typer.Argument(help="Run ID to replay")],
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Re-shape an archived HTML snapshot without re-fetching."""
    cfg = _load_config(data_dir)
    store = ReplayStore(cfg)
    record = asyncio.run(store.get(run_id))
    if not record:
        console.print(f"[red]run_id not found:[/red] {run_id}")
        raise typer.Exit(code=2)
    html = asyncio.run(store.load_html(run_id))
    if html is None:
        console.print("[red]no archived HTML for run[/red]")
        raise typer.Exit(code=2)
    doc = shape_document(html, record["url"])
    console.print(
        f"[green]OK[/green] replay {run_id[:12]}  "
        f"[dim]url={record['url']} chars={len(doc.markdown)}[/dim]"
    )
    console.print(doc.markdown[:4000])


@app.command()
def diff(
    run_a: Annotated[str, typer.Argument()],
    run_b: Annotated[str, typer.Argument()],
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Diff two recorded runs."""
    cfg = _load_config(data_dir)
    store = ReplayStore(cfg)
    report = asyncio.run(diff_runs(store, run_a, run_b))
    console.print(diff_summary(report))


@app.command()
def audit(
    n: Annotated[int, typer.Option(help="Tail this many lines")] = 50,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Tail the audit log."""
    cfg = _load_config(data_dir)
    from scrapo.policy.audit import AuditLog

    log = AuditLog(cfg.audit_log)
    events = asyncio.run(log.tail(n))
    for ev in events:
        console.print(json.dumps(ev, default=str))


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio."""
    from scrapo.agent.mcp_server import main

    main()


@app.command()
def adapters() -> None:
    """List registered proxy adapters."""
    from scrapo.access.adapters import registry
    from scrapo.access.adapters.brightdata import register_default as bd
    from scrapo.access.adapters.oxylabs import register_default as ox
    from scrapo.access.adapters.scrapfly import register_default as sf
    from scrapo.access.adapters.zyte import register_default as zy

    bd()
    ox()
    sf()
    zy()
    for name in registry.list_names():
        console.print(f"- {name}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Host to bind")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind")] = 8787,
    max_tier: Annotated[int, typer.Option(help="Default max tier for UI scrapes")] = 1,
    data_dir: Annotated[Path | None, typer.Option(help="Override data dir")] = None,
) -> None:
    """Run the local browser UI."""
    cfg = _load_config(data_dir)
    from scrapo.web import serve as serve_ui

    if host not in _LOOPBACK_HOSTS:
        console.print(
            f"[yellow]warning:[/yellow] binding the UI to {host} exposes an open scrape "
            "endpoint on your network. Anyone who can reach it can make this host fetch "
            "arbitrary URLs. Use 127.0.0.1 unless you know what you're doing."
        )

    serve_ui(
        cfg,
        host=host,
        port=port,
        max_tier=Tier(max_tier),
        on_ready=lambda url: console.print(f"[green]Scrapo View[/green] listening at {url}"),
    )


def main() -> None:
    configure_logging()
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
