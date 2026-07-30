from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse


def validate_webhook_request(request, token: str):
    max_bytes = int(getattr(settings, "WEBHOOK_MAX_BYTES", 1024 * 1024))
    content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    if content_length > max_bytes:
        return JsonResponse({"ok": False, "error": "payload_too_large"}, status=413)

    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "unknown")).split(",")[0].strip()
    window = 60
    limit = int(getattr(settings, "WEBHOOK_RATE_LIMIT_PER_MINUTE", 180))
    key = f"webhook-rate:{token}:{ip}"
    try:
        count = cache.get(key, 0)
        if count >= limit:
            return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
        if count:
            cache.incr(key)
        else:
            cache.set(key, 1, timeout=window)
    except Exception:
        # Webhooks must keep working if cache is temporarily unavailable.
        pass
    return None
