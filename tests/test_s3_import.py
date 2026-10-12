import boto3
from moto import mock_aws
from wks_core.storage.database import Client


def test_operator_import_requires_registered_prefix_and_preserves_original(env, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_PROFILE", raising=False)
    with mock_aws():
        remote = boto3.client("s3", region_name="us-east-1")
        remote.create_bucket(Bucket="wks-import")
        remote.put_object(
            Bucket="wks-import",
            Key="allowed/original.txt",
            Body=b"registered imported original",
            ContentType="text/plain",
        )
        body = {
            "namespace_id": env["ns_a"],
            "bucket": "wks-import",
            "object_key": "allowed/original.txt",
            "title": "Imported",
        }
        assert (
            env["http"]
            .post(
                "/v1/imports/s3", headers=env["headers"] | {"Idempotency-Key": "import"}, json=body
            )
            .status_code
            == 403
        )
        with env["s"].sessions.begin() as db:
            db.get(Client, env["a_id"]).operator = True
        assert (
            env["http"]
            .post(
                "/v1/imports/s3", headers=env["headers"] | {"Idempotency-Key": "import"}, json=body
            )
            .status_code
            == 403
        )
        env["s"].settings.s3_import_prefixes = [{"bucket": "wks-import", "prefix": "allowed/"}]
        imported = env["http"].post(
            "/v1/imports/s3", headers=env["headers"] | {"Idempotency-Key": "import"}, json=body
        )
        assert imported.status_code == 200
        receipt = imported.json()
        original = env["http"].get(
            f"/v1/sources/{receipt['source_id']}/versions/{receipt['source_version_id']}/original",
            headers=env["headers"],
        )
        assert original.content == b"registered imported original"
        replay = env["http"].post(
            "/v1/imports/s3", headers=env["headers"] | {"Idempotency-Key": "import"}, json=body
        )
        assert replay.json() == receipt
