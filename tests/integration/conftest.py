"""Fixtures for the real-browser integration suite.

These tests are the ones the offline suite can't cover: they launch a real
headless Chromium against a tiny local fixture server and exercise the browser
tier, deterministic actions (including the new auto-pagination verbs), and the
end-to-end metadata extraction path.

Everything here is gated. The whole module is marked ``integration`` (deselected
by default — see ``pyproject.toml``), and :func:`_browser_guard` skips when
Playwright or its Chromium build isn't installed, so selecting the suite on a
machine without a browser degrades to skips rather than errors.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from collections.abc import Iterator

import pytest

from scrapo.config import Config

# --- fixture pages ---------------------------------------------------------

_SPA = """\
<!doctype html><html><head><title>SPA</title></head>
<body><div id="app">loading…</div>
<script>
document.getElementById('app').innerHTML =
  '<h1>Rendered Heading</h1><p>content from javascript</p>';
</script></body></html>
"""

# Each scroll event appends items until a cap; each "Load more" click appends a
# batch and removes the button past a threshold. Deterministic enough to assert on.
_INFINITE = """\
<!doctype html><html><head><title>Infinite</title></head><body>
<div id="list"><div class="item">item 0</div></div>
<button id="more">Load more</button>
<script>
let n = 1;
// Each batch also grows the page, so the next wheel has new distance to travel
// and fires another scroll event (a real infinite feed gets taller as it loads).
function add(k){ for(let i=0;i<k;i++){ const d=document.createElement('div');
  d.className='item'; d.textContent='item '+(n++);
  document.getElementById('list').appendChild(d); }
  document.body.style.minHeight = (document.body.scrollHeight + 1500) + 'px'; }
window.addEventListener('scroll', () => { if (n < 30) add(5); });
document.getElementById('more').addEventListener('click', () => {
  add(5); if (n >= 20) { const b=document.getElementById('more'); if(b) b.remove(); }
});
document.body.style.minHeight = '2500px';
</script></body></html>
"""

_PRODUCT = """\
<!doctype html><html><head><title>Prod</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Integration Widget",
 "offers":{"@type":"Offer","price":"19.99","priceCurrency":"USD"}}
</script></head><body><h1>unrelated</h1></body></html>
"""

_PAGES = {"/": _SPA, "/infinite": _INFINITE, "/product": _PRODUCT}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = _PAGES.get(self.path.split("?", 1)[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:  # silence the per-request logging
        pass


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@pytest.fixture
def live_config(tmp_path) -> Config:
    # The fixture server is on loopback, so the SSRF guard must be told to allow it.
    return Config(data_dir=tmp_path / "scrapo", allow_private_hosts=True)


def _browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:  # noqa: BLE001 - missing browser build / sandbox issues → skip
        return False


@pytest.fixture(scope="session")
def _browser_ok() -> bool:
    return _browser_available()


@pytest.fixture(autouse=True)
def _browser_guard(_browser_ok: bool) -> None:
    if not _browser_ok:
        pytest.skip("Playwright Chromium is not installed (pip install 'scrapo[browser]' && playwright install chromium)")
