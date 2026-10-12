# Third-party software and models

WKS source, SDKs, CLI and worker adapters are Apache-2.0. The root LICENSE and
NOTICE are included in Python packages and standalone client archives. Apache-2.0
does not relicense dependencies, operating-system utilities or model weights.

Each release includes `sbom.cdx.json`, generated from the exact reviewed uv lock
and Go manifests, plus hashed profile requirements and source. Optional profiles
are included in that inventory; installing the API does not install decoders.
Unknown dependency license metadata is omitted rather than invented. Preserve
upstream license files when redistributing installed dependencies or container
layers. The inventory is not a vulnerability scan or legal approval of every
possible distribution arrangement.

| Component | Upstream license / authoritative source | Distribution |
| --- | --- | --- |
| Go runtime/toolchain | BSD-3-Clause, https://go.dev/LICENSE | Runtime in standalone binaries |
| golang.org/x/sys | BSD-3-Clause, upstream LICENSE | ACL Windows do CLI |
| gopkg.in/yaml.v3 | MIT and Apache-2.0, upstream LICENSE/NOTICE | CLI YAML parser |
| PostgreSQL / libpq | PostgreSQL License | Database image / Python driver runtime |
| Python | PSF License Agreement | Python runtime |
| FastAPI / Starlette / SQLAlchemy / Pydantic | MIT, respective upstream distributions | Core/API runtime |
| boto3 / botocore | Apache-2.0 | Optional S3 adapter runtime |
| Docling / docling-core | MIT, upstream distributions | Optional Docling profile |
| PyMuPDF | AGPL-3.0 or commercial license, https://pymupdf.readthedocs.io/en/latest/about.html | Native document profile; distributors must comply with its terms or obtain a commercial license |
| Pillow / openpyxl | HPND / MIT, upstream distributions | Native image/spreadsheet profile |
| Tesseract / libseccomp | Apache-2.0 / LGPL-2.1 | Worker operating-system utilities |
| FFmpeg | LGPL/GPL varies by configured build, https://ffmpeg.org/legal.html | Media profile; inspect the actual image's build configuration |
| faster-whisper / CTranslate2 | MIT, upstream distributions | Optional ASR profile |
| PyTorch / torchvision | BSD-3-Clause, upstream distributions | Optional Docling profile |

Model downloads use pinned revisions and SHA-256 manifests in
`scripts/download_models.py`; consult each model repository's license before
redistributing weights. Model weights are not embedded in the API or release
source archive. The exact installed profile and image determine the obligations.

For the full exact dependency versions, consult the release SBOM, `uv.lock`,
`go.sum` and profile requirements. OS packages are outside the language-lock
inventory and retain notices in their image layers. Source attribution and
license corrections can be reported through GitHub issues without sending data
or credentials.
