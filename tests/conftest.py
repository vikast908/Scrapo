"""Shared fixtures: isolated data dir per test, sample HTML."""

from __future__ import annotations

import pytest

from scrapo.config import Config, set_config


@pytest.fixture
def isolated_config(tmp_path):
    cfg = Config(data_dir=tmp_path / "scrapo")
    set_config(cfg)
    return cfg


@pytest.fixture
def sample_html() -> str:
    return """\
<!doctype html>
<html><head><title>Hello World — Demo</title></head>
<body>
  <header>nav junk</header>
  <main>
    <h1>Hello World</h1>
    <p>This is a <strong>demo</strong> page about <a href="/topic">a topic</a>.</p>
    <h2>Features</h2>
    <ul>
      <li>Fast</li>
      <li>Cheap</li>
      <li>Reliable</li>
    </ul>
    <h2>Pricing</h2>
    <p>Starting at <span class="price">$19/mo</span>.</p>
    <pre><code>print("hello")</code></pre>
    <table>
      <tr><th>Plan</th><th>Cost</th></tr>
      <tr><td>Basic</td><td>$19</td></tr>
      <tr><td>Pro</td><td>$49</td></tr>
    </table>
  </main>
  <footer>copyright junk</footer>
</body></html>
"""


@pytest.fixture
def blocked_html() -> str:
    return """\
<!doctype html>
<html><head><title>Just a moment...</title></head>
<body>
  <div id="cf-browser-verification">
    Checking your browser before accessing the site (cf-chl-bypass).
  </div>
</body></html>
"""
