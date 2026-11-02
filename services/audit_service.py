from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import AuditLog, SessionLocal


def log_event(user_id: int | None, action: str, module: str, entity_type: str | None = None, entity_id: int | None = None, details: dict[str, Any] | None = None) -> AuditLog:
    with SessionLocal() as session:
        record = AuditLog(
            user_id=user_id,
            action=action,
            module=module,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record
