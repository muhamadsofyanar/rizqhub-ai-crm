from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from django.utils import timezone

from crm.models import AutomationRule, AutomationRun


def _entity_id(obj) -> str:
    return str(getattr(obj, "pk", "none"))


def trigger_automations(
    trigger: str,
    *,
    tenant,
    brand=None,
    contact=None,
    conversation=None,
    deal=None,
    payload: dict[str, Any] | None = None,
    event_key: str | None = None,
):
    from crm.tasks import execute_automation_run

    rules = AutomationRule.objects.filter(tenant=tenant, trigger=trigger, is_active=True)
    if brand:
        rules = rules.filter(models.Q(brand=brand) | models.Q(brand__isnull=True))
    else:
        rules = rules.filter(brand__isnull=True)

    payload = payload or {}
    queued = []
    for rule in rules:
        base = event_key or ":".join(
            [
                trigger,
                _entity_id(conversation or deal or contact),
                str(payload.get("message_id") or payload.get("stage_id") or ""),
            ]
        )
        raw_key = f"{rule.id}:{base}"
        unique_key = hashlib.sha256(raw_key.encode()).hexdigest()
        scheduled_for = timezone.now() + timedelta(minutes=rule.delay_minutes or 0)
        run, created = AutomationRun.objects.get_or_create(
            tenant=tenant,
            rule=rule,
            unique_key=unique_key,
            defaults={
                "contact": contact,
                "conversation": conversation,
                "deal": deal,
                "trigger_payload": payload,
                "scheduled_for": scheduled_for,
            },
        )
        if created:
            countdown = max(0, int((scheduled_for - timezone.now()).total_seconds()))
            execute_automation_run.apply_async(args=[str(run.id)], countdown=countdown)
            queued.append(run)
    return queued


def render_template(value: str, *, contact=None, conversation=None, deal=None) -> str:
    replacements = {
        "{{contact}}": str(contact or ""),
        "{{name}}": getattr(contact, "name", "") or "Pelanggan",
        "{{phone}}": getattr(contact, "phone", "") or "",
        "{{email}}": getattr(contact, "email", "") or "",
        "{{brand}}": str(getattr(conversation, "brand", "") or getattr(deal, "brand", "") or ""),
        "{{deal}}": getattr(deal, "title", "") or "",
    }
    result = str(value or "")
    for key, replacement in replacements.items():
        result = result.replace(key, str(replacement))
    return result


def validate_rule_config(action: str, config: dict[str, Any]):
    required = {
        "tag_contact": ["tag"],
        "create_task": ["title"],
        "send_whatsapp": ["message"],
        "call_webhook": ["url"],
        "move_stage": ["stage_id"],
    }.get(action, [])
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"Konfigurasi wajib belum diisi: {', '.join(missing)}")
    # Ensure JSON serializability and reasonable size.
    encoded = json.dumps(config)
    if len(encoded) > 20000:
        raise ValueError("Konfigurasi automation terlalu besar")
    return config


# Imported lazily above to avoid app-loading cycles.
from django.db import models  # noqa: E402
