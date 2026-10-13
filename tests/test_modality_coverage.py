"""Capability flags and extraction coverage cannot imply complete multimodal meaning."""

import subprocess
from pathlib import Path

from wks_core.domain.models import Error, ExtractedAsset, ExtractedRepresentation
from wks_worker.worker import Worker
from wks_worker_media.processing import AudioProcessor


def test_real_asr_silence_has_no_speech_and_no_audio_semantics(env):
    path = env["tmp"] / "silence.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1",
            str(path),
        ],
        check=True,
    )
    settings = env["s"].settings.model_copy(
        update={
            "asr_enabled": True,
            "asr_model_path": str(Path(".local/models/asr-tiny").resolve()),
        }
    )
    result = AudioProcessor(settings).extract(path, "audio/wav")
    assert result.blocks == []
    assert result.coverage["audio"]["state"] == "partial"
    assert result.coverage["transcription"]["state"] == "no_speech_detected"
    assert result.coverage["audio_semantics"]["state"] == "not_interpreted"


def test_visual_enrichment_reports_partial_and_unmeasured_semantics(env, monkeypatch):
    from wks_core.storage.enrichment import HTTPMediaEnricher

    env["s"].settings.enrichment_endpoint = "https://fixture.example.test/enrich"
    calls = []

    def description(self, content, mime, operation):
        calls.append(operation)
        if len(calls) == 2:
            raise Error("processing.enrichment_unavailable", "fixture provider unavailable")
        return {
            "description": "Fixture response; not a qualified vision model",
            "producer": {"name": "fixture"},
        }

    monkeypatch.setattr(HTTPMediaEnricher, "enrich", description)
    worker = Worker(env["s"], queues=("native",))
    result = ExtractedRepresentation(
        assets=[
            ExtractedAsset(b"one", "image/png", "image", {}),
            ExtractedAsset(b"two", "image/png", "image", {}),
        ]
    )
    worker.enrich({"version_id": "fixture-version", "config": {}}, result)
    assert result.coverage["visual"]["state"] == "partial"
    assert result.coverage["visual"]["assets_enriched"] == 1
    assert result.coverage["visual"]["semantic_completeness"] == "not_measured"
    assert result.blocks[0].origin_kind == "ai_description"
    empty = ExtractedRepresentation()
    worker.enrich({"version_id": "fixture-version", "config": {}}, empty)
    assert empty.coverage["visual"]["state"] == "not_applicable"
