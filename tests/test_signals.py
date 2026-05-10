from scrapo.access.signals import detect_block, is_spa_shell, is_thin


def test_cloudflare_block_detected(blocked_html):
    blocked, reason = detect_block(blocked_html, status=200)
    assert blocked
    assert reason == "cloudflare"


def test_clean_page_not_blocked(sample_html):
    blocked, reason = detect_block(sample_html, status=200)
    assert not blocked
    assert reason is None


def test_429_is_block():
    blocked, reason = detect_block("<html><body>ok</body></html>", status=429)
    assert blocked
    assert reason == "http-429"


def test_403_is_block():
    blocked, reason = detect_block("<html><body>ok</body></html>", status=403)
    assert blocked


def test_empty_body_is_block():
    blocked, reason = detect_block("   ", status=200)
    assert blocked
    assert reason == "empty-body"


def test_is_thin():
    assert is_thin("hi")
    assert not is_thin("x" * 500)


def test_spa_shell_detected():
    shell = (
        "<!doctype html><html><head><title>App</title></head><body>"
        '<div id="root"></div>'
        + "".join(f'<script src="/static/chunk{i}.js"></script>' for i in range(6))
        + "<!-- " + "x" * 2000 + " -->"
        "</body></html>"
    )
    assert is_spa_shell(shell)


def test_rendered_page_is_not_spa_shell(sample_html):
    assert not is_spa_shell(sample_html)


def test_small_page_is_not_spa_shell():
    assert not is_spa_shell("<html><body><div id='root'></div></body></html>")
