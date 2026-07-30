from django.conf import settings
from django.db import transaction
from django.utils import timezone
from crm.models import Contact, Conversation, Message


def normalize_phone(value: str) -> str:
    value = "".join(ch for ch in str(value or "") if ch.isdigit())
    if value.startswith("0"):
        value = "62" + value[1:]
    return value


def parse_starsender(payload: dict) -> dict:
    return {
        "from": normalize_phone(payload.get("from") or payload.get("sender") or payload.get("number")),
        "name": payload.get("push_name") or payload.get("name") or "",
        "message": payload.get("message") or payload.get("body") or payload.get("text") or "",
        "file": payload.get("file") or payload.get("media") or "",
        "message_id": str(payload.get("message_id") or payload.get("id") or ""),
        "device": str(payload.get("device_id") or payload.get("device") or ""),
        "is_me": bool(payload.get("is_me", False)),
        "is_group": bool(payload.get("is_group", False)),
    }


@transaction.atomic
def store_inbound(connection, payload: dict):
    parsed = parse_starsender(payload)
    if parsed["is_me"] or parsed["is_group"] or not parsed["from"]:
        return None, None, parsed
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
    elif parsed["name"] and not contact.name:
        contact.name = parsed["name"]
        contact.save(update_fields=["name", "updated_at"])
    conversation = Conversation.objects.filter(
        tenant=connection.tenant,
        contact=contact,
        connection=connection,
        status__in=["open", "pending"],
    ).first()
    if not conversation:
        agent = connection.agent
        ai_default = bool(agent and agent.mode in ["limited", "autonomous"] and settings.AUTO_REPLY_DEFAULT)
        conversation = Conversation.objects.create(
            tenant=connection.tenant,
            contact=contact,
            brand=connection.brand,
            agent=agent,
            connection=connection,
            channel="whatsapp",
            ai_enabled=ai_default,
        )
    if parsed["message_id"]:
        existing = Message.objects.filter(tenant=connection.tenant, provider_message_id=parsed["message_id"]).first()
        if existing:
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
    conversation.save(update_fields=["last_message_at", "unread_count", "updated_at"])
    contact.last_activity_at = timezone.now()
    contact.save(update_fields=["last_activity_at", "updated_at"])
    return conversation, message, parsed
