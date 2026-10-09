import json
from pathlib import Path

import pytest
from conftest import process_all, register_text, upload
from wks_core.domain.models import Error
from wks_core.storage.content import detect_mime, validate_zip
from wks_worker_docling.processing import DoclingProcessor
from wks_worker_media.processing import AudioProcessor, VideoProcessor
from wks_worker_native.processing import (
    ImageProcessor,
    TextProcessor,
)

FIXTURES = Path("tests/fixtures")


def test_A11_A12_A13_A14_pdf_text_ocr_assets(env):
    r = upload(env, FIXTURES / "mixed.pdf", "application/pdf")
    process_all(env)
    status = env["s"].status(env["a"], r["operation_id"])
    assert status["processing_state"] in {"partial", "succeeded"}
    page = env["s"].read(env["a"], status["published_representation_id"])
    assert any(
        b["origin_kind"] == "native_text" and "paralelo" in b["text"] for b in page["blocks"]
    )
    assert any(b["origin_kind"] == "ocr" and "Parallel" in b["text"] for b in page["blocks"])
    hits = env["s"].search(env["a"], {"query": "paralelo"})["items"]
    assert hits and hits[0]["locator"]["kind"] == "page"
    refs = [ref for hit in hits for ref in hit["asset_refs"]]
    assert refs
    for ref in refs:
        aid = ref.rsplit("/", 1)[-1]
        meta = env["s"].asset_metadata(env["a"], aid)
        response = env["http"].get(meta["content_path"], headers=env["headers"])
        assert response.status_code == 200
        assert response.content.startswith(b"\x89PNG")
        assert (
            env["http"]
            .get(meta["content_path"], headers={"Authorization": "Bearer " + env["b_token"]})
            .status_code
            == 404
        )
    assert "wks://assets/" in page["markdown"] and "http" not in page["markdown"]


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("circuit.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (
            "circuit.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_office_native_preserves_assets_and_structure(env, filename, mime):
    r = upload(env, FIXTURES / filename, mime)
    process_all(env)
    status = env["s"].status(env["a"], r["operation_id"])
    assert status["processing_state"] == "succeeded"
    read = env["s"].read(env["a"], status["published_representation_id"])
    assert any(b["type"] == "picture_ref" for b in read["blocks"])
    assert env["s"].search(env["a"], {"query": "paralelo"})["items"]


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("circuit.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (
            "circuit.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_docling_office_adapter(env, filename, mime):
    pytest.importorskip("docling")
    out = DoclingProcessor(env["s"].settings).extract(FIXTURES / filename, mime)
    assert out.processor_name == "docling" and out.blocks
    assert any("paralelo" in b.text for b in out.blocks)


def test_A36_ocr_unavailable_retains_image(env):
    settings = env["s"].settings.model_copy(update={"ocr_language": "nonexistent_language"})
    out = ImageProcessor(settings).extract(FIXTURES / "circuit.png", "image/png")
    assert out.assets and out.assets[0].content == (FIXTURES / "circuit.png").read_bytes()
    assert out.availability == "text_partial" and "ocr_provider_unavailable" in out.warnings
    register_text(env)
    process_all(env)
    assert env["s"].search(env["a"], {"query": "paralelo"})["items"]


def test_image_ocr_and_metadata(env):
    r = upload(env, FIXTURES / "circuit.png", "image/png")
    process_all(env)
    assert env["s"].status(env["a"], r["operation_id"])["processing_state"] == "succeeded"
    assert env["s"].search(env["a"], {"query": "circuit"})["items"]


def test_text_invalid_json_and_encoding_metadata_only(env):
    path = env["tmp"] / "bad.json"
    path.write_text("{not json}")
    result = TextProcessor().extract(path, "application/json")
    assert result.availability == "metadata_only" and not result.blocks
    path.write_bytes(b"\xff\xff")
    assert TextProcessor().extract(path, "text/plain").availability == "metadata_only"


def test_mime_does_not_trust_filename(env):
    assert detect_mime(FIXTURES / "mixed.pdf", "text/html") == "application/pdf"
    assert detect_mime(FIXTURES / "circuit.png", "text/plain") == "image/png"


def test_zip_traversal_and_expansion_rejected(env):
    import zipfile

    path = env["tmp"] / "bad.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("../../escape", "x")
    with pytest.raises(Error, match="Unsafe"):
        validate_zip(path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("large", b"0" * (2 * 1024 * 1024))
    with pytest.raises(Error):
        validate_zip(path)


def test_A29_asr_disabled_is_explicit(env):
    out = AudioProcessor(env["s"].settings).extract(Path("unused"), "audio/wav")
    assert out.coverage["audio"]["state"] == "pending"
    assert out.warnings == ["asr_provider_unavailable"]


def test_A30_video_sample_real_timestamps(env):
    import subprocess

    path = env["tmp"] / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x120:rate=2:duration=2",
            "-pix_fmt",
            "yuv420p",
            "-threads",
            "1",
            str(path),
        ],
        check=True,
    )
    settings = env["s"].settings.model_copy(update={"video_enabled": True, "video_max_frames": 2})
    out = VideoProcessor(settings).extract(path, "video/mp4")
    assert len(out.assets) == 2
    assert [a.locator["time_ms"] for a in out.assets] == [0, 1000]
    assert out.coverage["visual"]["state"] == "sampled"
    assert out.coverage["audio"]["state"] == "not_applicable"


def test_fixture_hashes():
    import hashlib

    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    for filename, expected in manifest["files"].items():
        assert hashlib.sha256((FIXTURES / filename).read_bytes()).hexdigest() == expected


def test_docling_pdf_local_models_preserve_ocr_and_pages(env):
    pytest.importorskip("docling")
    root = Path(".local/models/docling").resolve()
    if not (root / "wks-model-manifest.json").exists():
        pytest.skip("Pinned Docling PDF models are not installed")
    settings = env["s"].settings.model_copy(
        update={"docling_artifacts_path": str(root), "ocr_language": "eng"}
    )
    out = DoclingProcessor(settings).extract(FIXTURES / "mixed.pdf", "application/pdf")
    assert out.processor_name == "docling" and out.availability == "text_ready"
    assert any(b.origin_kind == "ocr" and "Parallel" in b.text for b in out.blocks)
    assert any(b.origin_kind == "native_text" and "paralelo" in b.text for b in out.blocks)
    assert len([a for a in out.assets if a.kind == "page"]) == 2
    assert all(b.asset_refs for b in out.blocks)
