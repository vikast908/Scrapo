import pytest

from scrapo._db import connect


@pytest.mark.asyncio
async def test_connect_enables_wal_and_busy_timeout(tmp_path):
    async with connect(tmp_path / "x.sqlite") as db:
        cur = await db.execute("PRAGMA journal_mode")
        assert (await cur.fetchone())[0].lower() == "wal"
        cur = await db.execute("PRAGMA busy_timeout")
        assert (await cur.fetchone())[0] >= 1000
