import os
import sys

from wks_api.server.config import Settings

account_journey = "--accounts" in sys.argv
os.environ["WKS_DATABASE_URL"] = Settings().database_url.rsplit("/", 1)[0] + (
    "/wks_accounts_browser_test" if account_journey else "/wks_browser_test"
)
os.environ["WKS_STORAGE_PATH"] = (
    ".local/accounts-browser-objects" if account_journey else ".local/browser-objects"
)
os.environ["WKS_HTTP_PORT"] = "8083" if account_journey else "8082"
os.environ["WKS_OCR_LANGUAGE"] = "eng"
executable = "wks-worker" if "--worker" in sys.argv else "wks"
os.execv(".venv/bin/" + executable, [executable] if "--worker" in sys.argv else [executable, "api"])
