"""Pluggable storage for replay snapshots (raw HTML, screenshots).

The replay store keeps metadata in SQLite but the actual page bodies live behind
a :class:`SnapshotStore`: locally under the data dir by default, or in S3 when
``snapshot_backend`` is an ``s3://bucket/prefix`` URI (requires the ``[s3]`` extra).
Each store returns an opaque locator string for what it wrote; that string is what
gets persisted in the ``runs`` table and handed back to load it.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, Protocol


class SnapshotStore(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, locator: str) -> bytes | None: ...


class LocalSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, locator: str) -> bytes | None:
        path = Path(locator)
        return path.read_bytes() if path.exists() else None


class S3SnapshotStore:
    def __init__(self, bucket: str, prefix: str = "", client: Any | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3")
        self._client: Any = client

    def _key(self, key: str) -> str:
        return f"{self.prefix}/{key}".lstrip("/") if self.prefix else key

    def put(self, key: str, data: bytes) -> str:
        full = self._key(key)
        self._client.put_object(Bucket=self.bucket, Key=full, Body=data)
        return f"s3://{self.bucket}/{full}"

    def get(self, locator: str) -> bytes | None:
        if not locator.startswith("s3://"):
            return None
        rest = locator[len("s3://") :]
        bucket, _, key = rest.partition("/")
        try:
            obj = self._client.get_object(Bucket=bucket, Key=key)
            body: bytes = obj["Body"].read()
            return body
        except Exception:
            return None


def from_backend(backend: str, *, local_root: Path) -> SnapshotStore:
    """Build a SnapshotStore from a config string: 'local' or 's3://bucket/prefix'."""
    if backend.startswith("s3://"):
        rest = backend[len("s3://") :]
        bucket, _, prefix = rest.partition("/")
        return S3SnapshotStore(bucket=bucket, prefix=prefix)
    return LocalSnapshotStore(local_root)


def gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=6)


def gunzip(data: bytes) -> bytes:
    return gzip.decompress(data)
