"""Reproducible synthetic format fixtures; no external/private source material."""

import hashlib
import json
import zipfile
from pathlib import Path

from openpyxl import Workbook
from PIL import Image

root = Path("tests/fixtures/stabilization")
root.mkdir(exist_ok=True)
workbook = Workbook()
sheet = workbook.active
sheet.title = "Budget"
sheet.append(["Item", "Amount", "Formula"])
sheet.append(["Resistance", 10, "=B2*2"])
workbook.save(root / "budget.xlsx")


def archive(path, files):
    with zipfile.ZipFile(path, "w") as output:
        for name, value in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            output.writestr(info, value)


archive(
    root / "budget.ods",
    {
        "mimetype": "application/vnd.oasis.opendocument.spreadsheet",
        "content.xml": """<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table table:name="Budget"><table:table-row><table:table-cell><text:p>Resistance</text:p></table:table-cell><table:table-cell table:formula="of:=10*2"><text:p>20</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet></office:body></office:document-content>""",
    },
)
archive(
    root / "circuit.epub",
    {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book/package.opf"/></rootfiles></container>',
        "book/package.opf": '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
        "book/chapter.xhtml": '<html><body><h1>Circuit chapter</h1><p>Parallel resistance in a circuit.</p><img src="circuit.png"/><script>hostile code must not be extracted</script></body></html>',
        "book/circuit.png": Path("tests/fixtures/circuit.png").read_bytes(),
    },
)
(root / "circuit.xml").write_text('<circuit><resistance unit="ohm">10</resistance></circuit>\n')
(root / "code.py").write_text("def resistance(a, b):\n    return 1 / (1/a + 1/b)\n")
(root / "circuit.html").write_text(
    '<html><body><h1>Circuit</h1><p>Resistance in parallel.</p><script>ignored hostile script</script><pre>print("safe text")</pre></body></html>'
)
(root / "circuit.json").write_text('{"resistance": 10, "unit": "ohm"}\n')
(root / "circuit.yaml").write_text("resistance: 10\nunit: ohm\n")
(root / "circuit.csv").write_text("item,value\nresistance,10\n")
with Image.open("tests/fixtures/circuit.png") as image:
    for extension, format in (("jpg", "JPEG"), ("tiff", "TIFF"), ("webp", "WEBP"), ("bmp", "BMP")):
        image.convert("RGB").save(root / ("circuit." + extension), format)
manifest = {
    "license": "CC0-1.0",
    "kind": "synthetic functional fixtures; not document-quality certification",
    "files": {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.iterdir())
        if path.name != "manifest.json"
    },
}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
