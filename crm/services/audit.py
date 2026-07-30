from __future__ import annotations

from typing import Any

from crm.models import AuditLog


def request_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log_audit(*, tenant, action: str, request=None, user=None, obj=None, metadata: dict[str, Any] | None = None):
    if request is not None:
        user = user or (request.user if request.user.is_authenticated else None)
        ip_address = request_ip(request)
    else:
        ip_address = None
    AuditLog.objects.create(
        tenant=tenant,
        user=user,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        metadata=metadata or {},
        ip_address=ip_address,
    )
