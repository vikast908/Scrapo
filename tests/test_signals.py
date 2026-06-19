from scrapo.access.signals import detect_block, is_spa_shell, is_thin


def test_cloudflare_block_detected(blocked_html):
    blocked, reason = detect_block(blocked_html, status=200)
    assert blocked
    assert reason == "cloudflare"


def test_clean_page_not_blocked(sample_html):
    blocked, reason = detect_block(sample_html, status=200)
    assert not blocked
    assert reason is None


def test_cloudflare_turnstile_iframe_detected():
    # Modern challenge: opaque iframe from the CDN, vendor name never in text.
    html = (
        "<html><body><div class='widget'>"
        '<iframe src="https://challenges.cloudflare.com/turnstile/v0/x"></iframe>'
        "</div></body></html>"
    )
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "cloudflare"


def test_hcaptcha_script_detected():
    html = '<html><head><script src="https://js.hcaptcha.com/1/api.js"></script></head><body>x</body></html>'
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "captcha"


def test_recaptcha_script_detected():
    html = '<html><body><script src="https://www.google.com/recaptcha/api.js"></script></body></html>'
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "captcha"


def test_arkose_funcaptcha_detected():
    html = '<html><body><script src="https://client-api.arkoselabs.com/v2/abc/api.js"></script></body></html>'
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "captcha"


def test_datadome_delivery_script_detected():
    html = '<html><body><script src="https://geo.captcha-delivery.com/captcha/"></script></body></html>'
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "datadome"


def test_perimeterx_cdn_script_detected():
    html = '<html><body><script src="https://captcha.px-cdn.net/abc/captcha.js"></script></body></html>'
    blocked, reason = detect_block(html, status=200)
    assert blocked
    assert reason == "perimeterx"


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


def test_is_thin_measures_visible_text_not_markup():
    # Mostly markup cruft, almost no rendered text -> thin despite long raw HTML.
    markup_heavy = (
        "<!doctype html><html><head>"
        + "".join(f'<meta name="m{i}" content="{"c" * 40}">' for i in range(30))
        + "</head><body><div class='wrap'><span></span></div>hi</body></html>"
    )
    assert len(markup_heavy) > 500
    assert is_thin(markup_heavy)


def test_is_thin_legit_short_text_with_tags_not_thin():
    # Short page but full of real visible words -> not thin.
    html = "<html><body><p>" + ("word " * 40) + "</p></body></html>"
    assert not is_thin(html)


def test_spa_shell_astro_island_detected():
    shell = (
        "<!doctype html><html><head><title>App</title></head><body>"
        "<astro-island uid='1'></astro-island>"
        + "<!-- " + "x" * 800 + " -->"
        + "</body></html>"
    )
    assert is_spa_shell(shell)


def test_spa_shell_module_bundle_detected():
    shell = (
        "<!doctype html><html><head><title>App</title>"
        '<script type="module" src="/@vite/client"></script>'
        '<script type="module" src="/src/main.tsx"></script>'
        "</head><body><div id='app'></div>"
        + "<!-- " + "x" * 800 + " -->"
        + "</body></html>"
    )
    assert is_spa_shell(shell)


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
