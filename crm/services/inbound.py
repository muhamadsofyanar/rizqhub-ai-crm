from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from crm.models import Contact, Conversation, Message
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


def parse_starsender(payload: dict) -> dict:
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    source = {**nested, **payload}
    return {
        "from": normalize_phone(source.get("from") or source.get("sender") or source.get("number") or source.get("phone")),
        "name": source.get("push_name") or source.get("pushName") or source.get("name") or "",
        "message": source.get("message") or source.get("body") or source.get("text") or source.get("caption") or "",
        "file": source.get("file") or source.get("media") or source.get("url") or "",
        "message_id": str(source.get("message_id") or source.get("messageId") or source.get("id") or ""),
        "device": str(source.get("device_id") or source.get("device") or source.get("deviceId") or ""),
        "is_me": bool(source.get("is_me", source.get("isMe", False))),
        "is_group": bool(source.get("is_group", source.get("isGroup", False))),
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
    # A pure delivery receipt usually has no sender and no body.
    return bool(updated and not parsed["from"] and not parsed["message"] and not parsed["file"])


def is_optout_message(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    return normalized in OPTOUT_KEYWORDS or any(
        phrase in normalized for phrase in ("jangan kirim promo", "berhenti berlangganan")
    )


@transaction.atomic
def store_inbound(connection, payload: dict):
    parsed = parse_starsender(payload)
    if parsed["is_me"] or parsed["is_group"] or not parsed["from"]:
        return None, None, parsed

    contact = Contact.objects.filter(tenant=connection.tenant, phone=parsed["from"]).first()
    contact_created = False
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

    conversation = Conversation.objects.filter(
        tenant=connection.tenant,
        contact=contact,
        connection=connection,
        status__in=["open", "pending"],
    ).first()
    conversation_created = False
    if not conversation:
        agent = connection.agent
        ai_default = bool(
            agent
            and agent.mode in ["limited", "autonomous"]
            and settings.AUTO_REPLY_DEFAULT
        )
        conversation = Conversation.objects.create(
            tenant=connection.tenant,
            contact=contact,
            brand=connection.brand,
            agent=agent,
            connection=connection,
            channel="whatsapp",
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
                }
            )
            return conversation, existing, parsed

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
    )
    conversation.last_message_at = timezone.now()
    conversation.unread_count += 1
    conversation.status = "open"
    conversation.save(update_fields=["last_message_at", "unread_count", "status", "updated_at"])

    contact.last_activity_at = timezone.now()
    if is_optout_message(parsed["message"]):
        contact.marketing_consent = False
        tags = list(contact.tags or [])
        if "opt-out" not in tags:
            tags.append("opt-out")
        contact.tags = tags
        contact.save(update_fields=["last_activity_at", "marketing_consent", "tags", "updated_at"])
    else:
        contact.save(update_fields=["last_activity_at", "updated_at"])

    deal, deal_created = ensure_open_deal(contact, connection.brand)
    parsed.update(
        {
            "contact_created": contact_created,
            "conversation_created": conversation_created,
            "deal_id": str(deal.id),
            "deal_created": deal_created,
            "optout": is_optout_message(parsed["message"]),
        }
    )
    return conversation, message, parsed
