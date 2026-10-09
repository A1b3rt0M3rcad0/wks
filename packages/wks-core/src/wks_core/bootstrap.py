from wks_core.application.service import Service
from wks_core.settings import Settings
from wks_core.storage.database import database
from wks_core.storage.storage import FilesystemStore, S3Store


def build(settings=None):
    settings = settings or Settings()
    if settings.storage_provider not in {"filesystem", "s3"}:
        raise ValueError("Unsupported storage provider")
    engine, sessions = database(settings.database_url)
    store = (
        S3Store(settings)
        if settings.storage_provider == "s3"
        else FilesystemStore(settings.storage_path)
    )
    service = Service(settings, sessions, store)
    return service, engine
