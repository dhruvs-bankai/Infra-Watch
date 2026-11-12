from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    session: Session,
    *,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            request_id=request_id,
        )
    )
