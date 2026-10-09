"""Deterministic queue selection from immutable version MIME and processing policy."""

QUEUES = ("native", "docling", "media")


def processing_queue(mime, extraction_profile):
    if mime.startswith(("audio/", "video/")):
        return "media"
    if extraction_profile == "docling" and (mime == "application/pdf" or "openxmlformats" in mime):
        return "docling"
    return "native"
