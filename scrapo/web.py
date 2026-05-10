"""Small local web UI for running Scrapo from a browser."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import structlog

from scrapo.api import scrape
from scrapo.config import Config
from scrapo.results import ScrapeResult
from scrapo.types import Budget, Tier

log = structlog.get_logger(__name__)

DEFAULT_PORT = 8787


def normalize_target(value: str) -> str:
    """Normalize a user-entered domain or URL into an HTTP(S) URL."""
    target = value.strip()
    if not target:
        raise ValueError("Enter a domain or URL.")
    if "://" not in target:
        target = f"https://{target}"

    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported.")
    if not parsed.netloc:
        raise ValueError("Enter a valid domain or URL.")
    return target


def public_result(result: ScrapeResult) -> dict[str, Any]:
    """Return the result fields the browser UI needs, excluding raw HTML."""
    chunks = result.get("chunks") or []
    public_chunks: list[dict[str, Any]] = []
    for chunk in chunks[:100]:
        provenance = chunk.get("provenance") or {}
        public_chunks.append(
            {
                "text": (chunk.get("text") or "")[:3000],
                "heading_trail": provenance.get("heading_trail") or [],
                "selector_path": provenance.get("selector_path") or "",
            }
        )

    return {
        "run_id": result.get("run_id"),
        "url": result.get("url"),
        "status": result.get("status"),
        "tier_used": result.get("tier_used"),
        "title": result.get("title"),
        "blocked": result.get("blocked", False),
        "block_reason": describe_block_reason(result.get("block_reason")),
        "elapsed_ms": result.get("elapsed_ms"),
        "markdown": result.get("markdown", ""),
        "chunk_count": len(chunks),
        "chunks": public_chunks,
    }


def describe_block_reason(reason: Any) -> str | None:
    if reason is None:
        return None
    reason_text = str(reason)
    if reason_text == "robots":
        return "Blocked by robots.txt policy."
    if reason_text.startswith("geo-policy-violation:"):
        region = reason_text.removeprefix("geo-policy-violation:")
        if not region or region == "None":
            region = "unknown region"
        return f"Blocked by geo policy: {region}."
    return reason_text


def make_handler(
    config: Config,
    default_max_tier: Tier,
    scrape_lock: threading.Lock,
    allowed_hosts: frozenset[str],
) -> type[BaseHTTPRequestHandler]:
    class ScrapoViewHandler(BaseHTTPRequestHandler):
        server_version = "ScrapoView/0.1"

        def _host_ok(self) -> bool:
            if not allowed_hosts:  # not loopback-bound; Host check disabled
                return True
            sent = (self.headers.get("Host", "") or "").rsplit(":", 1)[0].strip("[]").lower()
            return sent in allowed_hosts

        def do_GET(self) -> None:
            if not self._host_ok():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Host header not allowed"})
                return
            if self.path in {"/", "/index.html"}:
                self._send(HTTPStatus.OK, INDEX_HTML, "text/html; charset=utf-8")
                return
            if self.path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return
            if self.path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            if not self._host_ok():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "Host header not allowed"})
                return
            if self.path != "/api/scrape":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            try:
                payload = self._read_json()
                url = normalize_target(str(payload.get("domain") or payload.get("url") or ""))
                max_tier = _coerce_tier(payload.get("max_tier"), default_max_tier)
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            try:
                with scrape_lock:
                    result = asyncio.run(scrape(url, config=config, budget=Budget(max_tier=max_tier)))
            except Exception:
                log.exception("scrapo.web.scrape_failed", url=url)
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "Scrape failed; see server logs for details."},
                )
                return

            self._send_json(HTTPStatus.OK, public_result(result))

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("request body must be a JSON object")
            return parsed

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: str | bytes, content_type: str) -> None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

    return ScrapoViewHandler


def serve(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    max_tier: Tier,
    on_ready: Callable[[str], None] | None = None,
) -> None:
    # Handlers run one-per-thread; serialize scrapes so concurrent requests
    # don't trip over each other writing the same SQLite stores.
    scrape_lock = threading.Lock()
    _loopback = {"127.0.0.1", "localhost", "::1"}
    # Only enforce the Host-header allowlist on loopback binds (anti DNS-rebinding);
    # if the operator deliberately binds a public interface, leave it to them.
    allowed_hosts = (
        frozenset(_loopback | {host.lower().strip("[]")}) if host.lower().strip("[]") in _loopback else frozenset()
    )
    handler = make_handler(config, max_tier, scrape_lock, allowed_hosts)
    server = ThreadingHTTPServer((host, port), handler)
    if on_ready is not None:
        on_ready(f"http://{host}:{port}/")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _coerce_tier(value: Any, default: Tier) -> Tier:
    if value is None or value == "":
        return default
    try:
        return Tier(int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_tier must be an integer from 0 to 4.") from exc


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scrapo View</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8f5;
      --panel: #ffffff;
      --text: #17201b;
      --muted: #637066;
      --line: #dfe5dc;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --warn: #b45309;
      --bad: #b91c1c;
      --code: #111827;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    .shell {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      min-height: 100vh;
    }

    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfa;
      padding: 24px;
    }

    main {
      min-width: 0;
      padding: 24px;
    }

    h1, h2, h3, p { margin: 0; }

    h1 {
      font-size: 24px;
      line-height: 1.15;
      font-weight: 700;
    }

    .subhead {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }

    form {
      display: grid;
      gap: 14px;
      margin-top: 28px;
    }

    label {
      display: grid;
      gap: 7px;
      color: #2f3a33;
      font-size: 13px;
      font-weight: 650;
    }

    input, select, button {
      font: inherit;
      border-radius: 6px;
    }

    input, select {
      width: 100%;
      min-height: 42px;
      border: 1px solid #cfd8d1;
      background: #ffffff;
      color: var(--text);
      padding: 9px 11px;
      outline: none;
    }

    input:focus, select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14);
    }

    button {
      min-height: 42px;
      border: 1px solid transparent;
      background: var(--accent);
      color: #ffffff;
      padding: 9px 13px;
      font-weight: 700;
      cursor: pointer;
    }

    button:hover { background: var(--accent-strong); }
    button:disabled { cursor: wait; opacity: 0.65; }

    .status {
      margin-top: 18px;
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
    }

    .history {
      margin-top: 28px;
      display: grid;
      gap: 8px;
    }

    .history h2 {
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .history button {
      min-height: 0;
      width: 100%;
      border: 1px solid var(--line);
      background: #ffffff;
      color: var(--text);
      padding: 10px;
      text-align: left;
      font-weight: 600;
    }

    .history button:hover { border-color: #aebbb2; background: #f4f7f5; }

    .empty {
      min-height: calc(100vh - 48px);
      display: grid;
      place-items: center;
      color: var(--muted);
      text-align: center;
      border: 1px dashed #cfd8d1;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.55);
      padding: 24px;
    }

    .result {
      display: grid;
      gap: 18px;
    }

    .result-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
    }

    .result-title {
      min-width: 0;
      display: grid;
      gap: 6px;
    }

    .result-title h2 {
      font-size: 22px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }

    .result-title a {
      color: var(--accent-strong);
      font-size: 14px;
      overflow-wrap: anywhere;
      text-decoration: none;
    }

    .meta {
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
    }

    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
    }

    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }

    .metric strong {
      display: block;
      font-size: 16px;
      overflow-wrap: anywhere;
    }

    .tabs {
      display: flex;
      gap: 8px;
      border-bottom: 1px solid var(--line);
    }

    .tab {
      min-height: 36px;
      border: 1px solid transparent;
      border-bottom: none;
      background: transparent;
      color: var(--muted);
      padding: 8px 10px;
    }

    .tab.active {
      background: #ffffff;
      color: var(--text);
      border-color: var(--line);
    }

    .panel {
      min-height: 360px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }

    pre {
      margin: 0;
      padding: 18px;
      max-height: calc(100vh - 300px);
      min-height: 360px;
      overflow: auto;
      color: var(--code);
      font: 13px/1.55 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .chunks {
      display: grid;
      gap: 0;
    }

    .chunk {
      border-bottom: 1px solid var(--line);
      padding: 14px 16px;
    }

    .chunk:last-child { border-bottom: none; }

    .chunk-head {
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }

    .chunk-text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 14px;
      line-height: 1.5;
    }

    .error {
      color: var(--bad);
      font-weight: 650;
    }

    @media (max-width: 860px) {
      .shell { grid-template-columns: 1fr; }
      aside { border-right: none; border-bottom: 1px solid var(--line); }
      main { padding: 16px; }
      .meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .result-header { display: grid; }
      .empty { min-height: 280px; }
    }

    @media (max-width: 520px) {
      aside { padding: 18px; }
      .meta { grid-template-columns: 1fr; }
      .tabs { overflow-x: auto; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h1>Scrapo View</h1>
      <p class="subhead">Run a scrape, inspect markdown, and review generated chunks.</p>

      <form id="scrape-form">
        <label>
          Domain or URL
          <input id="domain" name="domain" autocomplete="url" placeholder="trinka.ai" required>
        </label>
        <label>
          Max tier
          <select id="max-tier" name="max_tier">
            <option value="0">0 - HTTP</option>
            <option value="1" selected>1 - HTTP session</option>
            <option value="2">2 - Browser fallback</option>
            <option value="3">3 - Stealth fallback</option>
            <option value="4">4 - Agent fallback</option>
          </select>
        </label>
        <button id="run-button" type="submit">Run scrape</button>
      </form>

      <div id="status" class="status"></div>
      <section class="history">
        <h2>Recent</h2>
        <div id="history"></div>
      </section>
    </aside>

    <main id="main">
      <div class="empty">
        <div>
          <h2>No scrape selected</h2>
          <p class="subhead">Enter a domain on the left to see the result here.</p>
        </div>
      </div>
    </main>
  </div>

  <script>
    const form = document.getElementById('scrape-form');
    const domainInput = document.getElementById('domain');
    const tierInput = document.getElementById('max-tier');
    const runButton = document.getElementById('run-button');
    const statusEl = document.getElementById('status');
    const historyEl = document.getElementById('history');
    const mainEl = document.getElementById('main');

    let results = [];
    let activeTab = 'markdown';

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      await runScrape();
    });

    async function runScrape() {
      const domain = domainInput.value.trim();
      if (!domain) return;

      setBusy(true, 'Running scrape...');
      try {
        const response = await fetch('/api/scrape', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ domain, max_tier: Number(tierInput.value) })
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || 'Scrape failed.');
        }
        results = [payload, ...results.filter((item) => item.run_id !== payload.run_id)].slice(0, 8);
        activeTab = 'markdown';
        renderHistory();
        renderResult(payload);
        statusEl.textContent = `Completed run ${shortId(payload.run_id)}`;
      } catch (error) {
        statusEl.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
      } finally {
        setBusy(false);
      }
    }

    function setBusy(isBusy, message) {
      runButton.disabled = isBusy;
      runButton.textContent = isBusy ? 'Running...' : 'Run scrape';
      if (message) statusEl.textContent = message;
    }

    function renderHistory() {
      historyEl.innerHTML = '';
      if (!results.length) {
        const p = document.createElement('p');
        p.className = 'subhead';
        p.textContent = 'No recent runs.';
        historyEl.appendChild(p);
        return;
      }
      for (const result of results) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `${result.status || 'blocked'} ${result.url || ''}`;
        button.title = result.url || '';
        button.addEventListener('click', () => renderResult(result));
        historyEl.appendChild(button);
      }
    }

    function renderResult(result) {
      const title = result.title || result.url || 'Scrape result';
      mainEl.innerHTML = `
        <section class="result">
          <div class="result-header">
            <div class="result-title">
              <h2>${escapeHtml(title)}</h2>
              <a href="${escapeAttr(result.url || '#')}" target="_blank" rel="noreferrer">${escapeHtml(result.url || '')}</a>
            </div>
          </div>
          <div class="meta">
            ${metric('Status', result.blocked ? 'Blocked' : String(result.status || '-'))}
            ${metric('Tier', result.tier_used || '-')}
            ${metric('Chunks', String(result.chunk_count || 0))}
            ${metric('Run ID', shortId(result.run_id))}
          </div>
          ${result.blocked ? `<div class="error">${escapeHtml(result.block_reason || 'Blocked')}</div>` : ''}
          <div class="tabs">
            ${tabButton('markdown', 'Markdown')}
            ${tabButton('chunks', 'Chunks')}
            ${tabButton('json', 'JSON')}
          </div>
          <div class="panel" id="content-panel"></div>
        </section>
      `;
      for (const button of mainEl.querySelectorAll('.tab')) {
        button.addEventListener('click', () => {
          activeTab = button.dataset.tab;
          renderResult(result);
        });
      }
      renderPanel(result);
    }

    function renderPanel(result) {
      const panel = document.getElementById('content-panel');
      if (activeTab === 'chunks') {
        panel.innerHTML = `<div class="chunks">${
          (result.chunks || []).map((chunk, index) => `
            <article class="chunk">
              <div class="chunk-head">Chunk ${index + 1}${chunk.heading_trail?.length ? ' - ' + escapeHtml(chunk.heading_trail.join(' / ')) : ''}</div>
              <div class="chunk-text">${escapeHtml(chunk.text || '')}</div>
            </article>
          `).join('') || '<article class="chunk"><div class="chunk-text">No chunks returned.</div></article>'
        }</div>`;
        return;
      }
      if (activeTab === 'json') {
        panel.innerHTML = `<pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
        return;
      }
      panel.innerHTML = `<pre>${escapeHtml(result.markdown || '')}</pre>`;
    }

    function metric(label, value) {
      return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
    }

    function tabButton(id, label) {
      const active = activeTab === id ? ' active' : '';
      return `<button type="button" class="tab${active}" data-tab="${id}">${escapeHtml(label)}</button>`;
    }

    function shortId(value) {
      return value ? String(value).slice(0, 12) : '-';
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, '&#96;');
    }

    renderHistory();
  </script>
</body>
</html>
"""
