import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import boto3


class FilesystemStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        identity = self.root / ".store-id"
        if not identity.exists():
            from uuid import uuid4

            try:
                fd = os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(str(uuid4()))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
        if identity.is_symlink():
            raise ValueError("Invalid store identity")
        self.identity = identity.read_text().strip()

    def path(self, key: str) -> Path:
        candidate = self.root / key
        for part in (candidate, *candidate.parents):
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError("Symlink object keys are not allowed")
        p = candidate.resolve()
        if not p.is_relative_to(self.root) or p == self.root:
            raise ValueError("Invalid object key")
        return p

    def put_file(self, key: str, path: Path):
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as out, path.open("rb") as source:
                # Preserve holes for zero regions; large resumable binary inputs need not
                # consume physical disk for empty extents. Nonzero data is copied unchanged.
                while chunk := source.read(1024 * 1024):
                    if chunk.count(0) == len(chunk):
                        out.seek(len(chunk), 1)
                    else:
                        out.write(chunk)
                out.truncate(out.tell())
                out.flush()
                os.fsync(out.fileno())
            # Unique object keys, immutable promotion; never replace an existing original.
            os.link(tmp, dest)
            fd_dir = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd_dir)
            finally:
                os.close(fd_dir)
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

    def list_prefix_page(self, prefix="", cursor=None, page_size=100):
        import sys

        if sys.platform == "linux":
            from wks_core.storage.directory_page import page

            return page(self, prefix, cursor, min(max(page_size, 1), 1000))
        # Object names have one namespace/UUID level. scandir bounds directory state.
        import os

        values = []
        for namespace in self.root.iterdir():
            if not namespace.is_dir() or namespace.is_symlink():
                continue
            with os.scandir(namespace) as entries:
                for entry in entries:
                    key = f"{namespace.name}/{entry.name}"
                    if (
                        entry.is_file(follow_symlinks=False)
                        and key.startswith(prefix)
                        and (not cursor or key > cursor)
                    ):
                        values.append(key)
                        if len(values) > page_size * 2:
                            values = sorted(values)[: page_size + 1]
        values = sorted(values)[: page_size + 1]
        return {
            "keys": values[:page_size],
            "next_cursor": values[page_size - 1] if len(values) > page_size else None,
        }

    def keys(self):
        result, cursor = [], None
        while True:
            page = self.list_prefix_page(cursor=cursor)
            result.extend(page["keys"])
            cursor = page["next_cursor"]
            if not cursor:
                return result

    def location(self):
        return {"provider": "filesystem", "store_id": self.identity}

    def capabilities(self):
        return {"immutable_put": True, "range": True, "checksum": "sha256", "paged_listing": True}

    def head(self, key):
        path = self.path(key)
        return {"size": path.stat().st_size, "sha256": None}

    def verify_checksum(self, key, sha256, size=None):
        path = self.path(key)
        if size is not None and path.stat().st_size != size:
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == sha256

    def ready(self):
        with tempfile.NamedTemporaryFile(dir=self.root) as f:
            f.write(b"ready")
            f.flush()
            return True


class S3Store:
    def __init__(self, settings):
        from urllib.parse import urlsplit

        if settings.s3_endpoint and (
            urlsplit(settings.s3_endpoint).username
            or urlsplit(settings.s3_endpoint).password
            or urlsplit(settings.s3_endpoint).query
        ):
            raise ValueError("S3 endpoint must not contain credentials or query parameters")
        self.bucket = settings.s3_bucket
        self.region, self.endpoint = settings.s3_region, settings.s3_endpoint
        self.multipart_threshold, self.part_bytes = (
            settings.s3_multipart_threshold,
            settings.s3_multipart_part_bytes,
        )
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint, region_name=settings.s3_region
        )

    def put_file(self, key, path):
        with Path(path).open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
            stream.seek(0)
            if Path(path).stat().st_size >= self.multipart_threshold:
                return self.put_multipart(key, stream, sha)
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=stream,
                ContentLength=Path(path).stat().st_size,
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
                Metadata={"wks-sha256": sha},
            )

    def put_multipart(self, key, stream, sha):
        import base64

        upload = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ChecksumAlgorithm="SHA256", Metadata={"wks-sha256": sha}
        )["UploadId"]
        try:
            parts = []
            while raw := stream.read(self.part_bytes):
                number = len(parts) + 1
                if number > 10000:
                    raise ValueError("S3 multipart part count exceeded")
                checksum = base64.b64encode(hashlib.sha256(raw).digest()).decode()
                result = self.client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload,
                    PartNumber=number,
                    Body=raw,
                    ChecksumSHA256=checksum,
                )
                parts.append(
                    {
                        "PartNumber": number,
                        "ETag": result["ETag"],
                        "ChecksumSHA256": result.get("ChecksumSHA256", checksum),
                    }
                )
            return self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
        except Exception:
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload)
            except Exception:
                pass
            raise

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

    def list_prefix_page(self, prefix="", cursor=None, page_size=100):
        args = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": min(page_size, 1000)}
        if cursor:
            args["ContinuationToken"] = cursor
        page = self.client.list_objects_v2(**args)
        return {
            "keys": [item["Key"] for item in page.get("Contents", [])],
            "next_cursor": page.get("NextContinuationToken"),
        }

    def keys(self):
        result, cursor = [], None
        while True:
            page = self.list_prefix_page(cursor=cursor)
            result.extend(page["keys"])
            cursor = page["next_cursor"]
            if not cursor:
                return result

    def location(self):
        return {
            "provider": "s3",
            "bucket": self.bucket,
            "region": self.region,
            "endpoint": self.endpoint,
        }

    def capabilities(self):
        return {
            "immutable_put": True,
            "range": True,
            "checksum": "sha256",
            "paged_listing": True,
            "multipart": True,
        }

    def head(self, key):
        value = self.client.head_object(Bucket=self.bucket, Key=key)
        return {
            "size": value["ContentLength"],
            "sha256": value.get("Metadata", {}).get("wks-sha256"),
            "object_version": value.get("VersionId"),
        }

    def verify_checksum(self, key, sha256, size=None):
        if size is not None and self.head(key)["size"] != size:
            return False
        digest = hashlib.sha256()
        for chunk in self.iter_bytes(key):
            digest.update(chunk)
        return digest.hexdigest() == sha256

    def ready(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True
