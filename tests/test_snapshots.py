import boto3
import pytest
from moto import mock_aws

from scrapo.config import Config
from scrapo.replay.snapshots import (
    LocalSnapshotStore,
    S3SnapshotStore,
    from_backend,
    gunzip,
    gz,
)
from scrapo.replay.store import ReplayStore
from scrapo.types import FetchResult, RunRecord, Tier


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


def test_local_snapshot_store_roundtrip(tmp_path):
    store = LocalSnapshotStore(tmp_path / "snaps")
    loc = store.put("run1.html.gz", gz(b"<html>hi</html>"))
    assert gunzip(store.get(loc)) == b"<html>hi</html>"
    assert store.get(str(tmp_path / "does-not-exist")) is None


def test_from_backend_picks_implementation(tmp_path):
    assert isinstance(from_backend("local", local_root=tmp_path), LocalSnapshotStore)
    s3 = from_backend("s3://my-bucket/some/prefix", local_root=tmp_path)
    assert isinstance(s3, S3SnapshotStore)
    assert s3.bucket == "my-bucket" and s3.prefix == "some/prefix"


def test_s3_snapshot_store_roundtrip(aws_env):
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="scrapo-test")
        store = S3SnapshotStore(bucket="scrapo-test", prefix="snaps")
        loc = store.put("run1.html.gz", gz(b"<html>x</html>"))
        assert loc == "s3://scrapo-test/snaps/run1.html.gz"
        assert gunzip(store.get(loc)) == b"<html>x</html>"
        assert store.get("s3://scrapo-test/nope.html.gz") is None
        assert store.get("/not/an/s3/uri") is None


@pytest.mark.asyncio
async def test_replay_store_uses_s3_backend(aws_env, tmp_path):
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="scrapo-rs")
        cfg = Config(data_dir=tmp_path / "d", snapshot_backend="s3://scrapo-rs/snaps")
        store = ReplayStore(cfg)
        rec = RunRecord.new("https://e.com/")
        fetch = FetchResult(
            url="https://e.com/", final_url="https://e.com/", status=200,
            html="<html>hello</html>", headers={}, tier_used=Tier.HTTP,
        )
        await store.record(rec, fetch, None)
        row = await store.get(rec.run_id)
        assert row["html_path"].startswith("s3://scrapo-rs/snaps/")
        assert await store.load_html(rec.run_id) == "<html>hello</html>"
