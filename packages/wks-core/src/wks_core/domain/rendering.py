"""Deterministic Markdown over canonical blocks; source markup remains untrusted."""

import re


def _escape(value):
    return re.sub(r"([\\`*{}\[\]<>])", r"\\\1", str(value))


def render_block(block, asset_paths=None):
    kind, value, data = block["type"], block.get("text", ""), block.get("data", {})
    if data.get("preview_only"):
        return value + "\n\n[Full canonical block](" + data["full_block_ref"] + ")"
    if kind == "heading":
        return (
            "#" * min(max(int(data.get("level", 2)), 1), 6)
            + " "
            + _escape(value).replace("\n", " ")
        )
    if kind == "code":
        runs = [len(match[0]) for match in re.finditer(r"`+", value)]
        fence = "`" * max(3, max(runs, default=0) + 1)
        language = re.sub(r"[^a-zA-Z0-9_+-]", "", data.get("language", ""))[:40]
        return f"{fence}{language}\n{value}\n{fence}"
    if kind == "table" and data.get("rows"):
        rows = data["rows"]
        columns = max((len(row) for row in rows), default=0)

        def row_text(row):
            return (
                "| "
                + " | ".join(
                    _escape(cell).replace("|", "\\|").replace("\n", "<br>")
                    for cell in [*row, *([""] * (columns - len(row)))]
                )
                + " |"
            )

        result = [row_text(rows[0]), "| " + " | ".join(["---"] * columns) + " |"]
        result.extend(row_text(row) for row in rows[1:])
        return "\n".join(result)
    if kind == "list":
        items = data.get("items", value.splitlines())
        ordered = data.get("ordered", False)
        return "\n".join(
            (f"{i}. " if ordered else "- ") + _escape(item) for i, item in enumerate(items, 1)
        )
    if kind == "equation":
        return "$$\n" + value + "\n$$" if data.get("format") == "latex" else _escape(value)
    if kind in {"picture_ref", "video_frame_ref"}:
        links = []
        for ref in block.get("asset_refs", []):
            target = (asset_paths or {}).get(ref, ref)
            caption = _escape(data.get("caption") or "Source asset")
            links.append(f"![{caption}]({target})")
        return _escape(value) + ("\n" if value else "") + "\n".join(links)
    if kind in {"transcript", "audio_transcript"}:
        locator = block.get("locator", {})
        start, end = locator.get("start_ms"), locator.get("end_ms")
        prefix = f"[{start}–{end} ms] " if start is not None else ""
        return prefix + _escape(value)
    return _escape(value)


def markdown(blocks, asset_paths=None):
    return "\n\n".join(render_block(block, asset_paths) for block in blocks)
