"""Versioned format catalogue shared by API and workers without decoder imports."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FormatSpec:
    format: str
    mime: str
    extractor_name: str
    queue: str
    modalities: tuple[str, ...]
    fixtures: tuple[str, ...]
    requires_models: bool = False
    optional: bool = False


class FormatRegistry:
    version = "1"

    def __init__(self):
        self.specs = (
            FormatSpec("txt", "text/plain", "text", "native", ("text",), ("test_workflow.py",)),
            FormatSpec(
                "markdown", "text/markdown", "text", "native", ("text",), ("portuguese.md",)
            ),
            FormatSpec(
                "html",
                "text/html",
                "html-offline",
                "native",
                ("text",),
                ("stabilization/circuit.html",),
            ),
            FormatSpec(
                "json",
                "application/json",
                "text",
                "native",
                ("text",),
                ("stabilization/circuit.json",),
            ),
            FormatSpec(
                "yaml",
                "application/yaml",
                "text",
                "native",
                ("text",),
                ("stabilization/circuit.yaml",),
            ),
            FormatSpec(
                "xml",
                "application/xml",
                "structured-text",
                "native",
                ("text",),
                ("stabilization/circuit.xml",),
            ),
            FormatSpec(
                "csv",
                "text/csv",
                "text",
                "native",
                ("text", "table"),
                ("stabilization/circuit.csv",),
            ),
            FormatSpec(
                "python",
                "text/x-python",
                "structured-text",
                "native",
                ("text", "code"),
                ("stabilization/code.py",),
            ),
            FormatSpec(
                "pdf", "application/pdf", "pdf", "native", ("text", "image", "ocr"), ("mixed.pdf",)
            ),
            FormatSpec(
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "office",
                "native",
                ("text", "table", "image"),
                ("circuit.docx",),
            ),
            FormatSpec(
                "pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "office",
                "native",
                ("text", "table", "image"),
                ("circuit.pptx",),
            ),
            FormatSpec(
                "xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "spreadsheet",
                "native",
                ("text", "table"),
                ("stabilization/budget.xlsx",),
            ),
            FormatSpec(
                "ods",
                "application/vnd.oasis.opendocument.spreadsheet",
                "spreadsheet",
                "native",
                ("text", "table"),
                ("stabilization/budget.ods",),
            ),
            FormatSpec(
                "epub",
                "application/epub+zip",
                "epub-offline",
                "native",
                ("text", "image"),
                ("stabilization/circuit.epub",),
            ),
            *(
                FormatSpec(name, mime, "image", "native", ("image", "ocr"), (fixture,))
                for name, mime, fixture in (
                    ("png", "image/png", "circuit.png"),
                    ("jpeg", "image/jpeg", "stabilization/circuit.jpg"),
                    ("tiff", "image/tiff", "stabilization/circuit.tiff"),
                    ("webp", "image/webp", "stabilization/circuit.webp"),
                    ("bmp", "image/bmp", "stabilization/circuit.bmp"),
                )
            ),
            FormatSpec(
                "wav",
                "audio/wav",
                "audio",
                "media",
                ("transcript",),
                ("speech.wav",),
                requires_models=True,
                optional=True,
            ),
            FormatSpec(
                "mp4",
                "video/mp4",
                "video",
                "media",
                ("frames", "transcript"),
                ("speech.mp4",),
                requires_models=True,
                optional=True,
            ),
        )

    def get(self, mime):
        return next((spec for spec in self.specs if spec.mime == mime), None)

    def public(
        self,
        healthy_queues,
        limits,
        optional_enabled=False,
        producer_versions=None,
        extraction_profile="native",
    ):
        from wks_core.processing import processing_queue

        def enabled(spec):
            return not spec.optional or (
                optional_enabled.get(spec.format, False)
                if isinstance(optional_enabled, dict)
                else optional_enabled
            )

        return [
            {
                **asdict(spec),
                "queue": processing_queue(spec.mime, extraction_profile),
                "modalities": list(spec.modalities),
                "fixtures": list(spec.fixtures),
                "detected_mime": spec.mime,
                "extractor_version": (producer_versions or {}).get(
                    processing_queue(spec.mime, extraction_profile)
                ),
                "can_store": True,
                "can_extract": processing_queue(spec.mime, extraction_profile) in healthy_queues
                and enabled(spec),
                "supported_modalities": list(spec.modalities),
                "max_input_size": limits["upload_max_bytes"],
                "max_pages": limits["process_max_pages"],
                "quality_level": "functional_fixtures; accuracy_not_measured",
                "availability": "enabled"
                if processing_queue(spec.mime, extraction_profile) in healthy_queues
                and enabled(spec)
                else "worker_or_profile_unavailable",
            }
            for spec in self.specs
        ]
