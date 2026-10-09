from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WKS_", env_file=".env", extra="ignore")
    env: str = "development"
    database_url: str = "postgresql+psycopg://wks:wks-local@127.0.0.1:55432/wks"
    storage_provider: str = "filesystem"
    storage_path: Path = Path(".local/objects")
    s3_bucket: str = "wks-content"
    s3_region: str = "us-east-1"
    s3_endpoint: str | None = None
    upload_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=5 * 1024 * 1024 * 1024)
    namespace_max_bytes: int = Field(default=1024 * 1024 * 1024, ge=1)
    process_max_pages: int = Field(default=200, ge=1, le=10000)
    processing_timeout_seconds: int = Field(default=300, ge=1)
    process_max_memory_mb: int = Field(default=4096, ge=256)
    job_lease_seconds: int = Field(default=360, ge=1)
    job_max_attempts: int = Field(default=3, ge=1)
    search_max_page_size: int = Field(default=50, ge=1, le=100)
    search_max_response_chars: int = Field(default=16000, ge=1024)
    cursor_ttl_seconds: int = Field(default=900, ge=1)
    cursor_secret: str = ""
    ocr_enabled: bool = True
    ocr_language: str = "por+eng"
    asr_enabled: bool = False
    asr_model_path: str | None = None
    audio_max_seconds: int = Field(default=600, ge=1)
    video_enabled: bool = False
    video_max_seconds: int = Field(default=120, ge=1)
    video_max_frames: int = Field(default=8, ge=1, le=100)
    web_capture_enabled: bool = False
    capture_timeout_seconds: int = Field(default=15, ge=1)
    capture_max_redirects: int = Field(default=3, ge=0, le=5)
    extraction_profile: str = "native"
    docling_artifacts_path: str | None = None
    enrichment_provider: str = "none"
    enrichment_endpoint: str | None = None
    enrichment_token: str | None = None
    enrichment_max_assets: int = Field(default=8, ge=1, le=100)
    mcp_enabled: bool = True
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "testserver"]
    mcp_allowed_origins: list[str] = []
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    log_level: str = "INFO"
