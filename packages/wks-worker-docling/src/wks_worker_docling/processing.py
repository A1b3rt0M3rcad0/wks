import io
from importlib.metadata import version
from pathlib import Path

from wks_core.domain.models import Block, ExtractedAsset, ExtractedRepresentation
from wks_core.storage.content import validate_zip
from wks_worker.processing import text_blocks
from wks_worker_native.processing import OfficeProcessor, PDFProcessor


class DoclingProcessor:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, path, mime):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if "openxmlformats" in mime:
            validate_zip(path)
        options = PdfPipelineOptions()
        if self.settings.docling_artifacts_path:
            options.artifacts_path = Path(self.settings.docling_artifacts_path)
        options.do_ocr = self.settings.ocr_enabled
        options.ocr_options = TesseractCliOcrOptions(lang=self.settings.ocr_language.split("+"))
        options.generate_picture_images = True
        options.generate_page_images = True
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        suffix = {"application/pdf": ".pdf"}
        if "wordprocessingml" in mime:
            suffix[mime] = ".docx"
        if "presentationml" in mime:
            suffix[mime] = ".pptx"
        import shutil
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp:
            source = Path(temp) / ("input" + suffix.get(mime, path.suffix))
            shutil.copyfile(path, source)
            converted = converter.convert(source, max_num_pages=self.settings.process_max_pages)
        doc = converted.document
        out = ExtractedRepresentation(
            processor_name="docling", processor_version=version("docling")
        )
        native_pages = {}
        if mime == "application/pdf":
            from pypdf import PdfReader

            native_pages = {
                i + 1: " ".join((page.extract_text() or "").split())
                for i, page in enumerate(PdfReader(path).pages)
            }
            # Page renders preserve evidence even when layout detection finds no picture.
            for number, page in doc.pages.items():
                if page.image:
                    buf = io.BytesIO()
                    page.image.pil_image.save(buf, "PNG")
                    out.assets.append(
                        ExtractedAsset(
                            buf.getvalue(),
                            "image/png",
                            "page",
                            {"kind": "page", "value": str(number)},
                        )
                    )
        for item, level in doc.iterate_items():
            provenance = item.prov[0] if getattr(item, "prov", None) else None
            loc = (
                {"kind": "page", "value": str(provenance.page_no)}
                if provenance
                else {"kind": "section", "value": str(len(out.blocks) + 1)}
            )
            if provenance and provenance.bbox:
                loc["bbox"] = provenance.bbox.model_dump(mode="json")
            label = str(item.label.value)
            value = getattr(item, "text", "")
            origin = "native_text"
            if (
                provenance
                and native_pages
                and (
                    not native_pages.get(provenance.page_no)
                    or (value and " ".join(value.split()) not in native_pages[provenance.page_no])
                )
            ):
                origin = "ocr"
            if label == "table":
                data = item.data.model_dump(mode="json")
                out.blocks.append(
                    Block(
                        "table",
                        item.export_to_markdown(doc=doc),
                        loc,
                        origin_kind=origin,
                        data=data,
                    )
                )
            elif label == "picture":
                image = item.get_image(doc)
                if image:
                    buf = io.BytesIO()
                    image.save(buf, "PNG")
                    a = ExtractedAsset(buf.getvalue(), "image/png", "figure", loc)
                    out.assets.append(a)
                    out.blocks.append(
                        Block("picture_ref", locator=loc, asset_refs=[f"wks://assets/{a.id}"])
                    )
            elif value:
                blocks = text_blocks(value, loc, origin)
                if label in {"section_header", "title"}:
                    for b in blocks:
                        b.type, b.data = "heading", {"level": min(level + 1, 6)}
                elif label == "formula":
                    for b in blocks:
                        b.type = "equation"
                out.blocks += blocks
        status = str(converted.status.value)
        out.availability = "text_ready" if status == "success" else "text_partial"
        out.coverage = {
            "text": {"state": "complete" if status == "success" else "partial"},
            "visual": {"state": "not_interpreted", "assets_identified": len(out.assets)},
        }
        out.warnings = [str(e.error_message)[:200] for e in converted.errors]
        # Associate same-page figures with text so search discovers media provenance.
        for block in out.blocks:
            block.asset_refs += [
                f"wks://assets/{a.id}"
                for a in out.assets
                if a.locator.get("value") == block.locator.get("value")
                and f"wks://assets/{a.id}" not in block.asset_refs
            ]
        return out


class DoclingWithFallback:
    def __init__(self, settings):
        self.settings = settings

    def extract(self, path, mime):
        try:
            return DoclingProcessor(self.settings).extract(path, mime)
        except Exception:
            # Model downloads/provider outages cannot erase deterministic native text.
            fallback = (
                PDFProcessor(self.settings) if mime == "application/pdf" else OfficeProcessor()
            )
            out = fallback.extract(path, mime)
            out.warnings.append("docling_unavailable_native_fallback_used")
            out.coverage["layout"] = {"state": "pending"}
            return out
