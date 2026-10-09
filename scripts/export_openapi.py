"""Export the API contract without creating tables or connecting to the database."""

import json
from pathlib import Path

from wks_api.http.app import create_app
from wks_api.server.bootstrap import build
from wks_api.server.config import Settings

service, engine = build(Settings())
try:
    contract = create_app(service, engine).openapi()
    Path("packages/wks-api/openapi.json").write_text(
        json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    )
finally:
    engine.dispose()
