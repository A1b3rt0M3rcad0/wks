"""Shared action windows use bounded hashes, not payloads or labels containing tenant IDs."""

import hashlib
from datetime import timedelta

from sqlalchemy import case
from sqlalchemy.dialects.postgresql import insert
from wks_core.domain.models import Error
from wks_core.storage.database import now
from wks_core.storage.stabilization import ActionWindow


class BudgetService:
    def rate_limit(self, db, p, action, namespace_id=None):
        limit = self.settings.action_requests_per_minute
        if limit == 0:
            return
        namespace_id = None  # A caller cannot evade its action window by inventing tenant IDs.
        key = hashlib.sha256(
            f"{p.client_id}:{namespace_id or p.context_namespace_id or ''}:{action}".encode()
        ).hexdigest()
        instant = now()
        expired = ActionWindow.started_at <= instant - timedelta(minutes=1)
        statement = (
            insert(ActionWindow)
            .values(key=key, started_at=instant, attempts=1)
            .on_conflict_do_update(
                index_elements=[ActionWindow.key],
                set_={
                    "started_at": case((expired, instant), else_=ActionWindow.started_at),
                    "attempts": case((expired, 1), else_=ActionWindow.attempts + 1),
                },
            )
            .returning(ActionWindow.attempts)
        )
        if db.scalar(statement) > limit:
            raise Error(
                "operation.rate_limited", "Action budget exceeded; try again later", 429, True
            )
