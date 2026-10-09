import json
import subprocess
from importlib.metadata import version
from pathlib import Path

from wks_core.domain.models import Block, Error, ExtractedAsset, ExtractedRepresentation


class AudioProcessor:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, path, mime):
        if not self.settings.asr_enabled or not self.settings.asr_model_path:
            return ExtractedRepresentation(
                availability="metadata_only",
                processor_name="audio",
                coverage={"audio": {"state": "pending"}},
                warnings=["asr_provider_unavailable"],
            )
        info = probe(path)
        duration = float(info["format"].get("duration", 0))
        if not duration or duration > self.settings.audio_max_seconds:
            raise Error("processing.duration_limit", "Audio duration outside configured limit")
        from faster_whisper import WhisperModel

        model = WhisperModel(
            self.settings.asr_model_path, device="cpu", compute_type="int8", local_files_only=True
        )
        segments, facts = model.transcribe(str(path), vad_filter=True)
        blocks = []
        for s in segments:
            loc = {
                "kind": "time_range",
                "start_ms": round(s.start * 1000),
                "end_ms": round(s.end * 1000),
            }
            blocks.append(
                Block(
                    "audio_transcript",
                    s.text.strip(),
                    loc,
                    origin_kind="transcript",
                    data={
                        "language": facts.language,
                        "avg_logprob": s.avg_logprob,
                        "no_speech_probability": s.no_speech_prob,
                        "quality": "uncertain"
                        if s.avg_logprob < -1
                        else "unverified_transcription",
                    },
                )
            )
        return ExtractedRepresentation(
            blocks=blocks,
            processor_name="faster-whisper",
            processor_version=version("faster-whisper"),
            warnings=["Transcription is probabilistic; non-speech audio is not interpreted"],
            coverage={
                "audio": {"state": "complete", "duration_seconds": duration},
                "text": {"state": "complete"},
            },
        )


def probe(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(p.stdout)


class VideoProcessor:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, path, mime):
        if not self.settings.video_enabled:
            return ExtractedRepresentation(
                availability="metadata_only",
                processor_name="video",
                coverage={"visual": {"state": "pending"}, "audio": {"state": "pending"}},
            )
        info = probe(path)
        duration = float(info["format"].get("duration", 0))
        if not duration or duration > self.settings.video_max_seconds:
            raise Error("processing.duration_limit", "Video duration outside configured limit")
        out = ExtractedRepresentation(
            processor_name="ffmpeg-video",
            availability="text_partial",
            coverage={"visual": {"state": "sampled", "duration_seconds": duration}},
        )
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp:
            for i in range(self.settings.video_max_frames):
                timestamp = duration * i / self.settings.video_max_frames
                target = Path(temp) / f"frame-{i}.png"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(timestamp),
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-threads",
                        "1",
                        str(target),
                    ],
                    timeout=30,
                    check=True,
                )
                if not target.exists():
                    continue
                loc = {"kind": "frame", "time_ms": round(timestamp * 1000), "sample_index": i}
                a = ExtractedAsset(target.read_bytes(), "image/png", "frame", loc)
                out.assets.append(a)
                out.blocks.append(
                    Block("video_frame_ref", locator=loc, asset_refs=[f"wks://assets/{a.id}"])
                )
            if any(s["codec_type"] == "audio" for s in info["streams"]):
                audio = Path(temp) / "audio.wav"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(path),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(audio),
                    ],
                    timeout=60,
                    check=True,
                )
                result = AudioProcessor(self.settings).extract(audio, "audio/wav")
                out.blocks += result.blocks
                out.coverage.update(result.coverage)
                out.warnings += result.warnings
            else:
                out.coverage["audio"] = {"state": "not_applicable"}
        out.coverage["visual"]["frames_sampled"] = len(out.assets)
        return out
