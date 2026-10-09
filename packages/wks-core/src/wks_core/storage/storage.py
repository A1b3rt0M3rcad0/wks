import os
import shutil
import tempfile
from pathlib import Path

import boto3


class FilesystemStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root) or p == self.root:
            raise ValueError("Invalid object key")
        return p

    def put_file(self, key: str, path: Path):
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as out, path.open("rb") as source:
                shutil.copyfileobj(source, out, 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            # Unique object keys, immutable promotion; never replace an existing original.
            os.link(tmp, dest)
        finally:
            os.unlink(tmp)

    def materialize(self, key, destination):
        shutil.copyfile(self.path(key), destination)

    def iter_bytes(self, key, start=0, end=None):
        with self.path(key).open("rb") as stream:
            stream.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                chunk = stream.read(min(65536, remaining) if remaining is not None else 65536)
                if not chunk:
                    break
                yield chunk
                if remaining is not None:
                    remaining -= len(chunk)

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)

    def exists(self, key):
        return self.path(key).is_file()

    def keys(self):
        return [str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()]

    def ready(self):
        with tempfile.NamedTemporaryFile(dir=self.root) as f:
            f.write(b"ready")
            f.flush()
            return True


class S3Store:
    def __init__(self, settings):
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint, region_name=settings.s3_region
        )

    def put_file(self, key, path):
        with Path(path).open("rb") as stream:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=stream,
                ContentLength=Path(path).stat().st_size,
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
            )

    def materialize(self, key, destination):
        self.client.download_file(self.bucket, key, str(destination))

    def iter_bytes(self, key, start=0, end=None):
        args = {"Bucket": self.bucket, "Key": key}
        if start or end is not None:
            args["Range"] = f"bytes={start}-{end if end is not None else ''}"
        body = self.client.get_object(**args)["Body"]
        try:
            yield from body.iter_chunks(chunk_size=65536)
        finally:
            body.close()

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey"}:
                return False
            raise

    def keys(self):
        pages = self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
        return [x["Key"] for page in pages for x in page.get("Contents", [])]

    def ready(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True
