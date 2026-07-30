from __future__ import annotations

from typing import Any


def extract_provider_message_id(data: Any) -> str:
    """Best-effort extraction across common provider response shapes."""
    if isinstance(data, dict):
        for key in (
            "message_id",
            "messageId",
            "id",
            "msgId",
            "key",
        ):
            value = data.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        for key in ("data", "message", "result", "response"):
            value = data.get(key)
            found = extract_provider_message_id(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = extract_provider_message_id(item)
            if found:
                return found
    return ""


def normalize_delivery_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "queued": "queued",
        "pending": "queued",
        "sent": "sent",
        "success": "sent",
        "delivered": "delivered",
        "delivery": "delivered",
        "read": "read",
        "seen": "read",
        "failed": "failed",
        "failure": "failed",
        "error": "failed",
        "undelivered": "failed",
    }
    return aliases.get(status, "")
