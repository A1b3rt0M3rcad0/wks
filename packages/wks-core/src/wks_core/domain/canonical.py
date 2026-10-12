"""Versioned canonical representation rules; portable and framework-free."""

import json

SCHEMA_VERSION = "1.0"
BLOCK_KINDS = frozenset(
    {
        "heading",
        "paragraph",
        "table",
        "list",
        "equation",
        "picture_ref",
        "code",
        "audio_transcript",
        "video_frame_ref",
        "metadata",
        "transcript",
    }
)


def validate_metadata(value, max_bytes=32768, max_depth=8):
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("Metadata must be a bounded JSON object")

    def visit(node, depth):
        if depth > max_depth:
            raise ValueError("Metadata depth exceeds limit")
        if isinstance(node, dict):
            if len(node) > 64 or any(not isinstance(k, str) or len(k) > 200 for k in node):
                raise ValueError("Metadata keys exceed limit")
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            if len(node) > 256:
                raise ValueError("Metadata list exceeds limit")
            for child in node:
                visit(child, depth + 1)
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise ValueError("Metadata must contain JSON values")

    visit(value, 0)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > max_bytes:
        raise ValueError("Metadata byte size exceeds limit")
    return value


def validate_block(block, ordinal):
    if block.get("type") not in BLOCK_KINDS:
        raise ValueError("Unsupported canonical block kind")
    if not isinstance(block.get("text", ""), str) or not isinstance(block.get("locator", {}), dict):
        raise ValueError("Invalid canonical block")
    confidence = block.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
    ):
        raise ValueError("Confidence must be a measured value between zero and one")
    validate_metadata(block.get("locator", {}), max_bytes=8192)
    return block | {"ordinal": ordinal, "kind": block["type"], "schema_version": SCHEMA_VERSION}
