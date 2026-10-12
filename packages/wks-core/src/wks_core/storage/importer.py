"""Operator-only S3 import copies registered prefixes through the normal ingest journal."""

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from wks_core.application.utilities import digest
from wks_core.domain.models import Error


def import_s3(service, p, key, namespace_id, bucket, object_key, title, external_ref=None):
    with service.sessions() as db:
        service.operator(db, p)
        service.write(db, p, namespace_id)
    if not any(
        entry.get("bucket") == bucket and object_key.startswith(entry.get("prefix", ""))
        for entry in service.settings.s3_import_prefixes
    ):
        raise Error(
            "storage.import_forbidden", "S3 object is outside the registered import prefixes", 403
        )
    import boto3

    client = boto3.client(
        "s3", region_name=service.settings.s3_region, endpoint_url=service.settings.s3_endpoint
    )
    metadata = client.head_object(Bucket=bucket, Key=object_key)
    if metadata["ContentLength"] > service.settings.upload_max_bytes:
        raise Error("upload.too_large", "Imported original exceeds installation input budget", 413)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "original"
        client.download_file(bucket, object_key, str(path))
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != metadata["ContentLength"]:
            raise Error("storage.import_changed", "Imported object changed during transfer", 409)
        receipt = service.register(
            p,
            key,
            {
                "namespace_id": namespace_id,
                "kind": "upload",
                "title": title,
                "external_ref": external_ref,
                "metadata": {
                    "import_origin": "registered_s3_prefix",
                    "import_ref": digest([namespace_id, bucket, object_key, title, external_ref]),
                },
                "upload": {
                    "filename": Path(object_key).name[:500] or "original",
                    "declared_media_type": metadata.get("ContentType", "application/octet-stream"),
                    "byte_size": path.stat().st_size,
                    "checksum_sha256": sha,
                },
            },
        )
        service.receive(p, receipt["upload_id"], path)
        return service.commit(p, key + "-commit", receipt["upload_id"])
