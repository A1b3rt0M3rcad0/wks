"""Input identification shared by ingestion and workers; no decoding dependencies."""

import zipfile
from pathlib import Path

from wks_core.domain.models import Error


def detect_mime(path, declared="application/octet-stream"):
    with path.open("rb") as f:
        head = f.read(8192)
    signatures = [
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF8", "image/gif"),
        (b"ID3", "audio/mpeg"),
        (b"fLaC", "audio/flac"),
        (b"OggS", "audio/ogg"),
    ]
    for signature, mime in signatures:
        if head.startswith(signature):
            return mime
    if head.startswith(b"RIFF"):
        if head[8:12] == b"WAVE":
            return "audio/wav"
        if head[8:12] == b"WEBP":
            return "image/webp"
    if head[4:8] == b"ftyp":
        return "audio/mp4" if head[8:12] in {b"M4A ", b"M4B "} else "video/mp4"
    if len(head) >= 2 and head[0] == 255 and head[1] & 0xE0 == 0xE0:
        return "audio/mpeg"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if any(n.startswith("word/") for n in names):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                if any(n.startswith("ppt/") for n in names):
                    return (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    )
        except zipfile.BadZipFile:
            pass
    if b"\0" not in head:
        try:
            head.decode("utf-8")
            if declared in {
                "text/plain",
                "text/markdown",
                "text/csv",
                "text/html",
                "application/json",
                "application/yaml",
                "text/yaml",
            }:
                return declared
            if head and all(b >= 32 or b in {9, 10, 13} for b in head):
                return "text/plain"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def validate_zip(path, max_bytes=128 * 1024 * 1024):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(x.file_size for x in infos) > max_bytes:
            raise Error("upload.content_invalid", "Archive expansion exceeds limit")
        for info in infos:
            if (
                Path(info.filename).is_absolute()
                or ".." in Path(info.filename).parts
                or info.file_size > max(1024 * 1024, info.compress_size * 200)
            ):
                raise Error("upload.content_invalid", "Unsafe archive")
