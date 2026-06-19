import asyncio

from scrapo.access.action_cache import ActionCache, _goal_hash, _host


def test_host_and_goal_hash_normalization():
    assert _host("https://Shop.Example.com:8443/path") == "shop.example.com"
    assert _host("http://user:pw@host.tld/x") == "host.tld"
    assert _goal_hash("  Log In  ") == _goal_hash("log in")
    assert _goal_hash("log in") != _goal_hash("log out")


async def test_put_get_roundtrip(tmp_path):
    cache = ActionCache(tmp_path / "a.sqlite")
    actions = [
        {"action": "type", "sel": "input#user", "text_target": "", "tag": "input", "text": "alice"},
        {"action": "click", "sel": "button#go", "text_target": "Sign in", "tag": "button"},
    ]
    await cache.put("https://site.tld/login", "log in", actions)
    assert await cache.get("https://site.tld/login", "log in") == actions
    # different host or goal -> miss
    assert await cache.get("https://other.tld/login", "log in") == []
    assert await cache.get("https://site.tld/login", "buy thing") == []


async def test_failure_count_and_invalidate(tmp_path):
    cache = ActionCache(tmp_path / "b.sqlite")
    await cache.put("https://s.tld/", "g", [{"action": "scroll"}])
    assert await cache.record_failure("https://s.tld/", "g") == 1
    assert await cache.record_failure("https://s.tld/", "g") == 2
    await cache.record_success("https://s.tld/", "g")  # resets failures
    assert await cache.record_failure("https://s.tld/", "g") == 1
    await cache.invalidate("https://s.tld/", "g")
    assert await cache.get("https://s.tld/", "g") == []
    # recording a failure for a missing key is a no-op, not an error
    assert await cache.record_failure("https://s.tld/", "g") == 0


async def test_put_overwrites_and_clears_failures(tmp_path):
    cache = ActionCache(tmp_path / "c.sqlite")
    await cache.put("https://s.tld/", "g", [{"action": "scroll"}])
    await cache.record_failure("https://s.tld/", "g")
    await cache.put("https://s.tld/", "g", [{"action": "goto", "text": "https://s.tld/next"}])
    assert await cache.get("https://s.tld/", "g") == [{"action": "goto", "text": "https://s.tld/next"}]
    # failures were reset by the re-put
    assert await cache.record_failure("https://s.tld/", "g") == 1


# --- atomic record_failure (Feature 3) ------------------------------------

async def test_record_failure_returns_atomic_post_increment_count(tmp_path):
    cache = ActionCache(tmp_path / "atomic.sqlite")
    await cache.put("https://s.tld/", "g", [{"action": "scroll"}])
    # Each call returns exactly the count it produced (1, 2, 3 ...), read inside
    # the same connection block as the UPDATE (RETURNING) so no separate SELECT
    # can observe a value some other writer changed in between.
    assert await cache.record_failure("https://s.tld/", "g") == 1
    assert await cache.record_failure("https://s.tld/", "g") == 2
    assert await cache.record_failure("https://s.tld/", "g") == 3


async def test_record_failure_not_corrupted_by_interleaved_put(tmp_path):
    """A concurrent put() that resets failure_count=0 must not make record_failure
    return a stale value: the count is read in the same statement as the UPDATE,
    so each record_failure reflects its own write, never a racing reset."""
    cache = ActionCache(tmp_path / "race.sqlite")
    url, goal = "https://s.tld/", "g"
    await cache.put(url, goal, [{"action": "scroll"}])

    # Hammer record_failure while a put() (which zeroes failures) lands concurrently.
    async def reset() -> None:
        await cache.put(url, goal, [{"action": "scroll"}])

    results = await asyncio.gather(
        cache.record_failure(url, goal),
        reset(),
        cache.record_failure(url, goal),
        reset(),
        cache.record_failure(url, goal),
    )
    counts = [results[0], results[2], results[4]]
    # Every returned count is a real post-increment value (>=1): never a 0 read
    # leaked from a racing reset between an UPDATE and a separate SELECT.
    assert all(c >= 1 for c in counts), counts
