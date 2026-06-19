"""Pluggable storage for replay snapshots (raw HTML, screenshots).

The replay store keeps metadata in SQLite but the actual page bodies live behind
a :class:`SnapshotStore`: locally under the data dir by default, or in S3 when
``snapshot_backend`` is an ``s3://bucket/prefix`` URI (requires the ``[s3]`` extra).
Each store returns an opaque locator string for what it wrote; that string is what
gets persisted in the ``runs`` table and handed back to load it.
"""

from __future__ import annotations

import contextlib
import gzip
import os
import uuid
from pathlib import Path
from typing import Any, Protocol, cast

import structlog

_log = structlog.get_logger(__name__)

# gzip level for HTML/JSON snapshots. 4 gives noticeably less CPU than the
# default 6 at a near-identical ratio on HTML (lots of repeated markup).
GZIP_LEVEL = 4


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
        # Atomic write: a crash mid-stream must not leave a half-written snapshot
        # with the SQLite row already pointing at it. Write to a sibling tempfile
        # and rename — rename on the same filesystem is atomic on POSIX and on
        # Windows (since Python 3.3, Path.replace uses MoveFileEx).
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
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
        except _client_error() as exc:
            # Genuine not-found is the expected "no snapshot" case → None.
            # Anything else (auth, throttling, transient network) is a real
            # error we must not silently swallow as "identical/no snapshot".
            response = getattr(exc, "response", {}) or {}
            error_code = response.get("Error", {}).get("Code")
            if error_code in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            _log.warning("s3_snapshot_get_failed", bucket=bucket, key=key, error_code=error_code)
            raise


def _client_error() -> type[BaseException]:
    """Lazily resolve ``botocore.exceptions.ClientError``.

    boto3/botocore is an optional dependency (the ``[s3]`` extra), so the import
    is guarded. If botocore is unavailable we fall back to a class that catches
    nothing meaningful from a non-S3 store, deliberately *not* widening to
    ``Exception``/``BaseException``.
    """
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        class _UnreachableError(Exception):
            pass

        return _UnreachableError
    return cast("type[BaseException]", ClientError)


def from_backend(backend: str, *, local_root: Path) -> SnapshotStore:
    """Build a SnapshotStore from a config string: 'local' or 's3://bucket/prefix'."""
    if backend.startswith("s3://"):
        rest = backend[len("s3://") :]
        bucket, _, prefix = rest.partition("/")
        return S3SnapshotStore(bucket=bucket, prefix=prefix)
    return LocalSnapshotStore(local_root)


def gz(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=GZIP_LEVEL)


def gunzip(data: bytes) -> bytes:
    return gzip.decompress(data)
