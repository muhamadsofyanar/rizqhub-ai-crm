from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import Contact, Conversation, Message, StarSenderDevice, WhatsAppGroup
from crm.services.messages import normalize_delivery_status
from crm.services.pipeline import ensure_open_deal


OPTOUT_KEYWORDS = {
    "stop",
    "berhenti",
    "unsubscribe",
    "hapus saya",
    "jangan kirim lagi",
    "tidak mau promo",
}


def normalize_phone(value: str) -> str:
    value = "".join(ch for ch in str(value or "") if ch.isdigit())
    if value.startswith("0"):
        value = "62" + value[1:]
    return value


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_starsender(payload: dict) -> dict:
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    source = {**nested, **payload}
    is_group = _as_bool(source.get("is_group", source.get("isGroup", False)))
    chat_type = str(source.get("chat_type") or source.get("chatType") or "").lower()
    if chat_type == "group":
        is_group = True
    raw_from = str(source.get("from") or source.get("sender") or source.get("number") or source.get("phone") or "")
    return {
        "from": raw_from if is_group else normalize_phone(raw_from),
        "name": source.get("push_name") or source.get("pushName") or source.get("name") or "",
        "group_name": source.get("group_name") or source.get("groupName") or source.get("push_name") or "",
        "participant": source.get("participant") or source.get("participant_name") or source.get("sender_name") or "",
        "message": source.get("message") or source.get("body") or source.get("text") or source.get("caption") or "",
        "file": source.get("file") or source.get("media") or source.get("url") or "",
        "message_id": str(source.get("message_id") or source.get("messageId") or source.get("id") or ""),
        "device": str(source.get("device_id") or source.get("deviceId") or source.get("device") or ""),
        "device_name": str(source.get("device_name") or source.get("deviceName") or ""),
        "is_me": _as_bool(source.get("is_me", source.get("isMe", False))),
        "is_group": is_group,
        "is_mentioned": _as_bool(source.get("is_mentioned", source.get("isMentioned", False))),
        "chat_type": "group" if is_group else "personal",
        "quoted_message": source.get("quoted_message") or source.get("quotedMessage") or {},
        "timestamp": source.get("timestamp"),
        "status": normalize_delivery_status(
            source.get("status") or source.get("message_status") or source.get("messageStatus")
        ),
    }


def apply_delivery_status(connection, payload: dict) -> bool:
    parsed = parse_starsender(payload)
    if not parsed["message_id"] or not parsed["status"]:
        return False
    updated = Message.objects.filter(
        tenant=connection.tenant,
        provider_message_id=parsed["message_id"],
        direction="outbound",
    ).update(status=parsed["status"], raw_payload=payload, updated_at=timezone.now())
    return bool(updated and not parsed["from"] and not parsed["message"] and not parsed["file"])


def is_optout_message(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    return normalized in OPTOUT_KEYWORDS or any(
        phrase in normalized for phrase in ("jangan kirim promo", "berhenti berlangganan")
    )


def _group_contact(device: StarSenderDevice, parsed: dict) -> tuple[WhatsAppGroup, Contact]:
    group_id = parsed["from"]
    group, _ = WhatsAppGroup.objects.get_or_create(
        tenant=device.tenant,
        device=device,
        external_group_id=group_id,
        defaults={
            "name": parsed["group_name"] or parsed["name"] or group_id,
            "last_message_at": timezone.now(),
            "last_synced_at": timezone.now(),
        },
    )
    dirty = []
    group_name = parsed["group_name"] or parsed["name"]
    if group_name and group.name != group_name:
        group.name = group_name
        dirty.append("name")
    group.last_message_at = timezone.now()
    dirty.append("last_message_at")

    if not group.contact_id:
        contact = Contact.objects.create(
            tenant=device.tenant,
            brand=device.brand,
            name=group.name,
            phone=group_id,
            source="WhatsApp Group StarSender",
            marketing_consent=False,
            custom_fields={"chat_type": "group", "group_id": group_id},
        )
        group.contact = contact
        dirty.append("contact")
    else:
        contact = group.contact
        contact.last_activity_at = timezone.now()
        contact.save(update_fields=["last_activity_at", "updated_at"])
    group.save(update_fields=[*dirty, "updated_at"])
    return group, contact


@transaction.atomic
def store_inbound(connection, payload: dict, *, device: StarSenderDevice | None = None):
    parsed = parse_starsender(payload)
    if parsed["is_me"] or not parsed["from"]:
        return None, None, parsed

    group = None
    contact_created = False
    if parsed["is_group"]:
        if not device:
            try:
                device = connection.starsender_device
            except Exception:
                device = None
        if not device:
            return None, None, {**parsed, "ignore_reason": "group_device_not_mapped"}
        group, contact = _group_contact(device, parsed)
    else:
        contact = Contact.objects.filter(tenant=connection.tenant, phone=parsed["from"]).first()
        if not contact:
            contact = Contact.objects.create(
                tenant=connection.tenant,
                brand=connection.brand,
                name=parsed["name"],
                phone=parsed["from"],
                source="WhatsApp StarSender",
                marketing_consent=False,
            )
            contact_created = True
        else:
            dirty = []
            if parsed["name"] and not contact.name:
                contact.name = parsed["name"]
                dirty.append("name")
            if not contact.brand:
                contact.brand = connection.brand
                dirty.append("brand")
            if dirty:
                contact.save(update_fields=[*dirty, "updated_at"])

    conversation_filter = {
        "tenant": connection.tenant,
        "contact": contact,
        "connection": connection,
        "status__in": ["open", "pending"],
    }
    if parsed["is_group"]:
        conversation_filter["external_thread_id"] = parsed["from"]
    conversation = Conversation.objects.filter(**conversation_filter).first()
    conversation_created = False
    if not conversation:
        agent = connection.agent
        ai_default = bool(
            agent and agent.mode in ["limited", "autonomous"] and settings.AUTO_REPLY_DEFAULT
        )
        if group:
            ai_default = group.ai_mode in ["mention", "autonomous", "draft"]
        conversation = Conversation.objects.create(
            tenant=connection.tenant,
            contact=contact,
            brand=connection.brand,
            agent=agent,
            connection=connection,
            channel="whatsapp_group" if parsed["is_group"] else "whatsapp",
            external_thread_id=parsed["from"] if parsed["is_group"] else "",
            ai_enabled=ai_default,
        )
        conversation_created = True

    if parsed["message_id"]:
        existing = Message.objects.filter(
            tenant=connection.tenant,
            provider_message_id=parsed["message_id"],
        ).first()
        if existing:
            parsed.update(
                {
                    "contact_created": contact_created,
                    "conversation_created": conversation_created,
                    "duplicate": True,
                    "group_id": str(group.id) if group else "",
                    "group_ai_mode": group.ai_mode if group else "",
                }
            )
            return conversation, existing, parsed

    metadata = {
        "chat_type": parsed["chat_type"],
        "participant": parsed["participant"],
        "is_mentioned": parsed["is_mentioned"],
        "quoted_message": parsed["quoted_message"],
        "group_id": str(group.id) if group else "",
    }
    message = Message.objects.create(
        tenant=connection.tenant,
        conversation=conversation,
        direction="inbound",
        sender_type="contact",
        body=parsed["message"],
        message_type="media" if parsed["file"] else "text",
        attachment_url=parsed["file"],
        provider_message_id=parsed["message_id"],
        status="delivered",
        raw_payload=payload,
        ai_metadata=metadata,
    )
    conversation.last_message_at = timezone.now()
    conversation.unread_count += 1
    conversation.status = "open"
    conversation.save(update_fields=["last_message_at", "unread_count", "status", "updated_at"])

    contact.last_activity_at = timezone.now()
    if not parsed["is_group"] and is_optout_message(parsed["message"]):
        contact.marketing_consent = False
        tags = list(contact.tags or [])
        if "opt-out" not in tags:
            tags.append("opt-out")
        contact.tags = tags
        contact.save(update_fields=["last_activity_at", "marketing_consent", "tags", "updated_at"])
    else:
        contact.save(update_fields=["last_activity_at", "updated_at"])

    deal = None
    deal_created = False
    if not parsed["is_group"]:
        deal, deal_created = ensure_open_deal(contact, connection.brand)
    parsed.update(
        {
            "contact_created": contact_created,
            "conversation_created": conversation_created,
            "deal_id": str(deal.id) if deal else "",
            "deal_created": deal_created,
            "optout": (not parsed["is_group"]) and is_optout_message(parsed["message"]),
            "group_id": str(group.id) if group else "",
            "group_ai_mode": group.ai_mode if group else "",
        }
    )
    return conversation, message, parsed
