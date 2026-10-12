"""Recoverable leases for asynchronous full exports."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from wks_core.storage.database import Base


class ExportLease(Base):
    __tablename__ = "export_leases"
    export_id: Mapped[str] = mapped_column(
        ForeignKey("export_artifacts.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String(36))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
