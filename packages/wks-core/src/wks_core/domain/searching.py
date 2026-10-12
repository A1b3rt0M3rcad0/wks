"""Portable lexical presentation and explicitly heuristic language selection."""

import re
import unicodedata


def language_hint(text, declared=None):
    if isinstance(declared, str):
        normalized = declared.lower().split("-")[0]
        if normalized in {"pt", "en", "simple"}:
            return normalized, "declared"
    words = set(re.findall(r"\w+", text.lower()))
    portuguese = len(
        words & {"para", "uma", "não", "são", "das", "dos", "com", "tensão", "resistência"}
    )
    english = len(words & {"the", "and", "with", "from", "this", "that", "resistance", "voltage"})
    if portuguese > english and portuguese:
        return "pt", "heuristic"
    if english > portuguese and english:
        return "en", "heuristic"
    return "simple", "unknown"


def snippet(text, query, max_chars=600):
    """Plain-text match window and character offsets; never inserts source HTML."""
    normalized = unicodedata.normalize("NFC", text)
    terms = [term.strip('"') for term in re.findall(r"[\wÀ-ÿ]+", query) if len(term) > 1]
    terms = terms[:32]
    matches = []
    # Approximate suffix stem matches affect only highlighting, never FTS membership/ranking.
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        matches.extend((match.start(), match.end()) for match in pattern.finditer(normalized))
    first = min((start for start, _ in matches), default=0)
    start = max(0, first - max_chars // 3)
    end = min(len(normalized), start + max_chars)
    output = normalized[start:end]
    positions = [
        [lo - start, hi - start] for lo, hi in sorted(set(matches)) if lo >= start and hi <= end
    ]
    return output, positions


def plain_headline(marked, start_marker, end_marker):
    output, positions, offset, active = [], [], 0, None
    token = re.compile(re.escape(start_marker) + "|" + re.escape(end_marker))
    last = 0
    for match in token.finditer(marked):
        part = marked[last : match.start()]
        output.append(part)
        offset += len(part)
        if match.group() == start_marker:
            active = offset
        elif active is not None:
            positions.append([active, offset])
            active = None
        last = match.end()
    output.append(marked[last:])
    return "".join(output), positions
