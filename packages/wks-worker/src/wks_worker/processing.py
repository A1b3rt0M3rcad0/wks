"""Bounded processor helpers and subprocess entry point."""

import json
from dataclasses import asdict
from pathlib import Path

from wks_core.domain.models import Block, Error
from wks_core.processing import processing_queue
from wks_core.settings import Settings


def text_blocks(value, locator=None, origin="native_text"):
    blocks, parent = [], None
    for line_no, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        loc = locator or {"kind": "line_range", "start_line": line_no, "end_line": line_no}
        for offset in range(0, len(line), 2000):
            part = line[offset : offset + 2000]
            b = Block("paragraph", part, loc, origin_kind=origin, parent_id=parent)
            if part.startswith("#") and offset == 0:
                level = len(part) - len(part.lstrip("#"))
                b.type, b.text, b.data = "heading", part.lstrip("# "), {"level": min(level, 6)}
                parent = b.id
            blocks.append(b)
    return blocks


def processor(settings, mime):
    queue = processing_queue(mime, settings.extraction_profile)
    if queue == "media":
        from wks_worker_media.processing import AudioProcessor, VideoProcessor

        return AudioProcessor(settings) if mime.startswith("audio/") else VideoProcessor(settings)
    if queue == "docling":
        from wks_worker_docling.processing import DoclingWithFallback

        return DoclingWithFallback(settings)
    from wks_worker_native.processing import processor as native_processor

    return native_processor(settings, mime)


def main():
    import sys

    path, mime, config_path, destination = sys.argv[1:]
    config = json.loads(Path(config_path).read_text())
    settings = Settings(**config)
    import resource

    memory_limit = settings.process_max_memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    cpu_limit = settings.processing_timeout_seconds + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 5))
    try:
        result = processor(settings, mime).extract(Path(path), mime)
        data = asdict(result)
        for asset in data["assets"]:
            target = Path(destination) / asset["id"]
            target.write_bytes(asset.pop("content"))
        (Path(destination) / "result.json").write_text(json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        code = exc.code if isinstance(exc, Error) else "processing.content_invalid"
        (Path(destination) / "error.json").write_text(json.dumps({"code": code}))
        raise


if __name__ == "__main__":
    main()
