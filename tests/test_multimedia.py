import importlib.util
from pathlib import Path

import pytest
from conftest import process_all, upload

MODEL = Path(".local/models/asr-tiny")


@pytest.mark.parametrize(
    "filename,mime", [("speech.wav", "audio/wav"), ("speech.mp4", "video/mp4")]
)
def test_A29_A30_real_asr_temporal_search_original_frames(env, filename, mime):
    if not MODEL.joinpath("wks-model-manifest.json").exists() or not importlib.util.find_spec(
        "faster_whisper"
    ):
        pytest.skip("Run uv sync --extra asr and scripts/download_models.py to qualify real ASR")
    s = env["s"]
    s.settings.asr_enabled, s.settings.asr_model_path = True, str(MODEL.resolve())
    s.settings.video_enabled, s.settings.video_max_frames = True, 2
    r = upload(env, Path("tests/fixtures") / filename, mime)
    process_all(env)
    status = s.status(env["a"], r["operation_id"])
    assert status["processing_state"] in {"succeeded", "partial"}, status
    hit = next(
        h
        for h in s.search(env["a"], {"query": "voltage"})["items"]
        if h["origin_kind"] == "transcript"
    )
    assert (
        hit["locator"]["kind"] == "time_range"
        and hit["locator"]["end_ms"] > hit["locator"]["start_ms"]
    )
    original = s.binary(env["a"], sid=r["source_id"], vid=r["source_version_id"])
    assert original["size"] == (Path("tests/fixtures") / filename).stat().st_size
    page = s.read(env["a"], status["published_representation_id"])
    assert any(b["origin_kind"] == "transcript" for b in page["blocks"])
    if mime == "video/mp4":
        frames = [b for b in page["blocks"] if b["type"] == "video_frame_ref"]
        assert len(frames) == 2 and frames[0]["locator"]["time_ms"] == 0
        ref = frames[0]["asset_refs"][0]
        assert s.asset_metadata(env["a"], ref.rsplit("/", 1)[-1])["media_type"] == "image/png"
