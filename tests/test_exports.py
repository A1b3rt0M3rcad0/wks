import hashlib
import io
import json
import zipfile

import pytest
from conftest import process_all, register_text, upload
from wks_core.domain.models import Error
from wks_core.domain.rendering import markdown


def test_markdown_preserves_tables_lists_code_and_untrusted_markup():
    blocks = [
        {"type": "heading", "text": "Document", "data": {"level": 1}},
        {
            "type": "table",
            "text": "",
            "data": {"rows": [["Column A", "Column B"], ["pipe|value", "line\nbreak"]]},
        },
        {
            "type": "code",
            "text": "```\n<script>alert(1)</script>",
            "data": {"language": "javascript"},
        },
        {"type": "list", "text": "", "data": {"items": ["First", "Second"], "ordered": True}},
        {"type": "paragraph", "text": "<script>hostile</script>"},
    ]
    rendered = markdown(blocks)
    assert "# Document" in rendered
    assert "| pipe\\|value | line<br>break |" in rendered
    assert "````javascript\n```\n<script>alert(1)</script>\n````" in rendered
    assert "1. First\n2. Second" in rendered
    assert "\\<script\\>hostile\\</script\\>" in rendered


def test_full_exports_are_immutable_complete_and_authorized(env):
    registered = register_text(
        env, value="\n\n".join(f"Paragraph {number}." for number in range(90))
    )
    process_all(env)
    rid = env["s"].status(env["a"], registered["operation_id"])["published_representation_id"]
    assert env["s"].read(env["a"], rid)["next_cursor"]
    ids = []
    for format in ("markdown-single", "structured-json", "plain-text", "source-original"):
        response = env["http"].post(
            f"/v1/representations/{rid}/exports",
            json={"format": format},
            headers=env["headers"] | {"Idempotency-Key": format},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        identity = response.json()["id"]
        ids.append(identity)
        assert env["s"].export_create(env["a"], format, rid, format)["id"] == identity
    process_all(env)
    for identity in ids:
        metadata = env["s"].export_get(env["a"], identity)
        assert metadata["status"] == "ready"
        response = env["http"].get(metadata["content_path"], headers=env["headers"])
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == metadata["sha256"]
        assert b"Paragraph 89." in response.content
        if metadata["format"] == "structured-json":
            assert len(response.json()["blocks"]) > 20
        with pytest.raises(Error):
            env["s"].export_get(env["b"], identity)
    env["s"].revoke(env["a"], "revoke", registered["source_id"])
    assert (
        env["http"].get(f"/v1/exports/{ids[0]}/content", headers=env["headers"]).status_code == 404
    )


def test_bundle_uses_relative_assets_and_verifiable_manifest(env):
    registered = upload(
        env,
        "tests/fixtures/circuit.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    process_all(env)
    rid = env["s"].status(env["a"], registered["operation_id"])["published_representation_id"]
    result = env["s"].export_create(env["a"], "bundle", rid, "markdown-bundle")
    process_all(env)
    metadata = env["s"].export_get(env["a"], result["id"])
    assert metadata["status"] == "ready"
    body = env["http"].get(metadata["content_path"], headers=env["headers"]).content
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["files"]) > 1
        text = archive.read("document.md").decode()
        assert "wks://assets/" not in text
        for item in manifest["files"]:
            assert not item["path"].startswith("/") and ".." not in item["path"].split("/")
            raw = archive.read(item["path"])
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
            assert len(raw) == item["byte_size"]
