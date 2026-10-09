import os
import sys

from wks_api.server.config import Settings

os.environ["WKS_DATABASE_URL"] = Settings().database_url.rsplit("/", 1)[0] + "/wks_browser_test"
os.environ["WKS_STORAGE_PATH"] = ".local/browser-objects"
os.environ["WKS_HTTP_PORT"] = "8082"
os.environ["WKS_OCR_LANGUAGE"] = "eng"
os.execv(".venv/bin/wks", ["wks", "worker" if "--worker" in sys.argv else "api"])
