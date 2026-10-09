from pydantic import Field
from wks_core.settings import Settings as CoreSettings


class Settings(CoreSettings):
    mcp_enabled: bool = True
    mcp_allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "testserver"]
    mcp_allowed_origins: list[str] = []
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    log_level: str = "INFO"
    web_public_origin: str | None = None
    web_session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
