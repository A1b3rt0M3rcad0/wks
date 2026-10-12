import hashlib

import boto3
import pytest
from moto import mock_aws
from wks_core.settings import Settings
from wks_core.storage.storage import S3Store


def test_s3_multipart_uses_full_sha_and_immutable_completion(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="wks-multipart")
        store = S3Store(
            Settings(
                s3_bucket="wks-multipart",
                s3_multipart_threshold=5 * 1024 * 1024,
                s3_multipart_part_bytes=5 * 1024 * 1024,
            )
        )
        path = tmp_path / "original"
        raw = b"multipart original" * (700000)
        path.write_bytes(raw)
        store.put_file("namespace/original", path)
        sha = hashlib.sha256(raw).hexdigest()
        assert store.head("namespace/original")["sha256"] == sha
        assert store.verify_checksum("namespace/original", sha, len(raw))
        assert (
            client.head_object(Bucket=store.bucket, Key="namespace/original")["ETag"].strip('"')
            != sha
        )
        path.write_bytes(b"changed" * (2000000))
        with pytest.raises(Exception):
            store.put_file("namespace/original", path)
        assert store.verify_checksum("namespace/original", sha, len(raw))
        assert not client.list_multipart_uploads(Bucket=store.bucket).get("Uploads")


def test_multipart_failure_aborts_remaining_parts(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="wks-multipart")
        store = S3Store(Settings(s3_bucket="wks-multipart", s3_multipart_threshold=5 * 1024 * 1024))
        path = tmp_path / "original"
        path.write_bytes(b"x" * (6 * 1024 * 1024))
        monkeypatch.setattr(
            store.client, "upload_part", lambda **_: (_ for _ in ()).throw(OSError("interrupted"))
        )
        with pytest.raises(OSError):
            store.put_file("namespace/original", path)
        assert not client.list_multipart_uploads(Bucket=store.bucket).get("Uploads")
        assert not store.exists("namespace/original")
