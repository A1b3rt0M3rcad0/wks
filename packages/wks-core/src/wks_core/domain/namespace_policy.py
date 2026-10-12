"""Small offline policy/schema vocabulary; never resolves external schema references."""

from wks_core.domain.canonical import validate_metadata

LIMITS = {
    "max_bytes": (1, 2**63 - 1),
    "max_input_bytes": (1, 5 * 1024**3),
    "max_pages": (1, 10000),
    "max_concurrent_jobs": (1, 32),
}
TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def validate_policy(policy):
    validate_metadata(policy)
    if (
        set(policy)
        - set(LIMITS)
        - {"allowed_profiles", "metadata_schema", "indexed_metadata_fields"}
    ):
        raise ValueError("Unsupported namespace policy")
    for key, (low, high) in LIMITS.items():
        if key in policy and (
            isinstance(policy[key], bool)
            or not isinstance(policy[key], int)
            or not low <= policy[key] <= high
        ):
            raise ValueError("Invalid namespace budget")
    profiles = policy.get("allowed_profiles", ["native", "docling", "media"])
    if not isinstance(profiles, list) or not set(profiles) <= {"native", "docling", "media"}:
        raise ValueError("Invalid processor profile list")
    fields = policy.get("indexed_metadata_fields", [])
    if not isinstance(fields, list) or not set(fields) <= {
        "category",
        "document_type",
        "declared_language",
    }:
        raise ValueError("Metadata filter fields must be indexed whitelist members")
    schema = policy.get("metadata_schema", {})
    if not isinstance(schema, dict) or set(schema) - {
        "required",
        "properties",
        "additionalProperties",
    }:
        raise ValueError("Only the offline metadata schema subset is supported")
    for definition in schema.get("properties", {}).values():
        if (
            not isinstance(definition, dict)
            or set(definition) - {"type", "enum", "maxLength"}
            or definition.get("type") not in TYPES
        ):
            raise ValueError("Invalid metadata property definition")
    return policy


def validate_source_metadata(metadata, policy):
    validate_metadata(metadata)
    schema = policy.get("metadata_schema", {})
    if any(key not in metadata for key in schema.get("required", [])):
        raise ValueError("Required metadata is absent")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False and set(metadata) - set(properties):
        raise ValueError("Unexpected metadata property")
    for key, value in metadata.items():
        if key not in properties:
            continue
        definition = properties[key]
        kind = definition["type"]
        if not isinstance(value, TYPES[kind]) or (
            kind in {"integer", "number"} and isinstance(value, bool)
        ):
            raise ValueError("Invalid metadata property type")
        if "enum" in definition and value not in definition["enum"]:
            raise ValueError("Metadata value is outside its enumeration")
        if isinstance(value, str) and len(value) > definition.get("maxLength", 32768):
            raise ValueError("Metadata string exceeds its schema limit")
