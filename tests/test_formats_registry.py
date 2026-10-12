import json
from dataclasses import asdict
from pathlib import Path

import pytest
from wks_core.domain.rendering import markdown
from wks_core.settings import Settings
from wks_core.storage.content import detect_mime
from wks_worker_native.processing import processor

ROOT = Path("tests/fixtures/stabilization")
MIMES = {
    "budget.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "budget.ods": "application/vnd.oasis.opendocument.spreadsheet",
    "circuit.epub": "application/epub+zip",
    "circuit.xml": "application/xml",
    "code.py": "text/x-python",
    "circuit.html": "text/html",
    "circuit.json": "application/json",
    "circuit.yaml": "application/yaml",
    "circuit.csv": "text/csv",
    "circuit.jpg": "image/jpeg",
    "circuit.tiff": "image/tiff",
    "circuit.webp": "image/webp",
    "circuit.bmp": "image/bmp",
}


@pytest.mark.parametrize("fixture,mime", MIMES.items())
def test_declared_formats_have_detected_fixtures_and_exportable_results(fixture, mime):
    path = ROOT / fixture
    assert detect_mime(path, mime) == mime
    result = processor(Settings(ocr_enabled=False), mime).extract(path, mime)
    assert result.blocks or result.assets
    serialized = json.dumps(asdict(result), default=lambda value: "binary")
    assert "confidence" not in serialized
    assert isinstance(markdown([asdict(block) for block in result.blocks]), str)
    if fixture == "budget.xlsx":
        assert "=B2*2" in serialized
    if fixture == "budget.ods":
        assert "of:=10*2" in serialized
    if fixture in {"circuit.epub", "circuit.html"}:
        assert "hostile" not in serialized
    if fixture == "circuit.epub":
        assert len(result.assets) == 1
        assert result.assets[0].locator["chapter"] == 1


def test_xml_entities_are_disabled(tmp_path):
    from wks_core.domain.models import Error

    path = tmp_path / "entity.xml"
    path.write_text('<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    with pytest.raises(Error):
        processor(Settings(), "application/xml").extract(path, "application/xml")


def test_spreadsheet_budget_returns_honest_partial():
    result = processor(Settings(process_max_table_rows=1), MIMES["budget.xlsx"]).extract(
        ROOT / "budget.xlsx", MIMES["budget.xlsx"]
    )
    assert result.availability == "text_partial"
    assert result.coverage["text"]["state"] == "partial"
    assert "spreadsheet_row_budget_reached" in result.warnings
