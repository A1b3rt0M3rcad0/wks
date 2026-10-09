"""Run real converters in a processor image and assert transport isolation."""

import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--component", choices=("native", "docling", "media"), required=True)
    args = parser.parse_args()
    script = """
import importlib.util, json
from pathlib import Path
from wks_core.settings import Settings
assert importlib.util.find_spec('wks_api') is None
assert importlib.util.find_spec('fastapi') is None
assert importlib.util.find_spec('mcp') is None
"""
    if args.component == "native":
        script += """
from wks_worker_native.processing import PDFProcessor
assert importlib.util.find_spec('wks_worker_media') is None
assert importlib.util.find_spec('wks_worker_docling') is None
result = PDFProcessor(Settings(ocr_language='eng')).extract(Path('/fixtures/mixed.pdf'), 'application/pdf')
assert any(b.origin_kind == 'native_text' and 'paralelo' in b.text for b in result.blocks)
assert any(b.origin_kind == 'ocr' and 'Parallel' in b.text for b in result.blocks)
assert result.assets
"""
    elif args.component == "docling":
        script += """
from wks_worker_docling.processing import DoclingProcessor
assert importlib.util.find_spec('wks_worker_media') is None
result = DoclingProcessor(Settings(docling_artifacts_path='/models/docling', ocr_language='eng')).extract(Path('/fixtures/mixed.pdf'), 'application/pdf')
assert result.processor_name == 'docling'
assert any(b.origin_kind == 'ocr' for b in result.blocks)
assert result.assets
"""
    else:
        script += """
from wks_worker_media.processing import AudioProcessor, VideoProcessor
assert importlib.util.find_spec('wks_worker_native') is None
assert importlib.util.find_spec('wks_worker_docling') is None
settings = Settings(asr_enabled=True, asr_model_path='/models/asr-tiny', video_enabled=True, video_max_frames=2)
audio = AudioProcessor(settings).extract(Path('/fixtures/speech.wav'), 'audio/wav')
assert any(b.type == 'audio_transcript' for b in audio.blocks)
result = VideoProcessor(settings).extract(Path('/fixtures/speech.mp4'), 'video/mp4')
assert len(result.assets) == 2
assert any(b.type == 'audio_transcript' for b in result.blocks)
"""
    script += "\nprint(json.dumps({'processor': result.processor_name, 'blocks':len(result.blocks), 'assets':len(result.assets)}))\n"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "6g",
            "--mount",
            f"type=bind,src={Path('tests/fixtures').resolve()},dst=/fixtures,readonly",
            "--mount",
            f"type=bind,src={Path('.local/models').resolve()},dst=/models,readonly",
            "-e",
            "HF_HUB_OFFLINE=1",
            "-e",
            "OMP_NUM_THREADS=2",
            args.image,
            "python",
            "-c",
            script,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
