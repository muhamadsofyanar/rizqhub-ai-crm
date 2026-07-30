from celery import shared_task
from django.utils import timezone
from crm.models import ChannelConnection, Conversation, Message, WebhookEvent
from crm.services.ai import generate_reply
from crm.services.inbound import store_inbound
from crm.services.providers import send_starsender


def _needs_handoff(agent, text: str) -> tuple[bool, str]:
    if not agent:
        return True, "Agent belum dipetakan"
    words = [x.strip().lower() for x in agent.handoff_keywords.split(",") if x.strip()]
    lowered = (text or "").lower()
    for word in words:
        if word and word in lowered:
            return True, f"Kata eskalasi terdeteksi: {word}"
    return False, ""


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 4})
def process_starsender_event(self, event_id: str):
    event = WebhookEvent.objects.select_related("connection", "connection__agent").get(id=event_id)
    event.attempts += 1
    event.status = "processing"
    event.save(update_fields=["attempts", "status", "updated_at"])
    conversation, inbound, parsed = store_inbound(event.connection, event.payload)
    if not conversation or not inbound:
        event.status = "ignored"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at", "updated_at"])
        return
    agent = conversation.agent
    handoff, reason = _needs_handoff(agent, inbound.body)
    if handoff:
        conversation.needs_handoff = True
        conversation.handoff_reason = reason
        conversation.ai_enabled = False
        conversation.save(update_fields=["needs_handoff", "handoff_reason", "ai_enabled", "updated_at"])
    elif conversation.ai_enabled and agent:
        reply, metadata = generate_reply(conversation, inbound.body)
        if agent.mode in ["draft", "approval", "human"]:
            Message.objects.create(
                tenant=conversation.tenant,
                conversation=conversation,
                direction="internal",
                sender_type="ai",
                body=reply,
                status="queued",
                ai_metadata={**metadata, "draft": True},
            )
            conversation.needs_handoff = True
            conversation.handoff_reason = "Draft AI menunggu persetujuan"
            conversation.save(update_fields=["needs_handoff", "handoff_reason", "updated_at"])
        else:
            response = send_starsender(event.connection, conversation.contact.phone, reply)
            Message.objects.create(
                tenant=conversation.tenant,
                conversation=conversation,
                direction="outbound",
                sender_type="ai",
                body=reply,
                status="sent",
                raw_payload=response if isinstance(response, dict) else {},
                ai_metadata=metadata,
            )
            conversation.last_message_at = timezone.now()
            conversation.save(update_fields=["last_message_at", "updated_at"])
    event.status = "processed"
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at", "updated_at"])


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 4})
def send_whatsapp_message(self, message_id: str):
    message = Message.objects.select_related("conversation__connection", "conversation__contact").get(id=message_id)
    connection = message.conversation.connection
    result = send_starsender(connection, message.conversation.contact.phone, message.body, message.attachment_url)
    message.status = "sent"
    message.raw_payload = result if isinstance(result, dict) else {}
    message.save(update_fields=["status", "raw_payload", "updated_at"])

@shared_task
def launch_campaign(campaign_id: str):
    from crm.models import Campaign, CampaignRecipient, Contact
    campaign = Campaign.objects.select_related("connection", "brand").get(id=campaign_id)
    campaign.status = "running"
    campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])
    contacts = Contact.objects.filter(
        tenant=campaign.tenant,
        brand=campaign.brand,
        marketing_consent=True,
    )
    if campaign.channel == "whatsapp":
        contacts = contacts.exclude(phone="")
    else:
        contacts = contacts.exclude(email="")
    if campaign.tag_filter:
        contacts = contacts.filter(tags__contains=[campaign.tag_filter])
    contacts = list(contacts[:500])
    if not contacts:
        campaign.status = "completed"
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["status", "completed_at", "updated_at"])
        return
    delay_seconds = int((campaign.connection.settings or {}).get("campaign_delay_seconds", 3))
    for index, contact in enumerate(contacts):
        recipient, _ = CampaignRecipient.objects.get_or_create(
            tenant=campaign.tenant,
            campaign=campaign,
            contact=contact,
        )
        send_campaign_recipient.apply_async(args=[str(recipient.id)], countdown=min(index * delay_seconds, 1800))


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_campaign_recipient(self, recipient_id: str):
    from django.db.models import F
    from crm.models import Campaign, CampaignRecipient
    from crm.services.providers import send_mailketing
    recipient = CampaignRecipient.objects.select_related("campaign__connection", "contact", "campaign").get(id=recipient_id)
    if recipient.status == "sent":
        return
    campaign = recipient.campaign
    contact = recipient.contact
    replacements = {
        "{{name}}": contact.name or "Pelanggan",
        "{{phone}}": contact.phone or "",
        "{{email}}": contact.email or "",
        "{{company}}": contact.company or "",
    }
    body = campaign.content
    subject = campaign.subject
    for key, value in replacements.items():
        body = body.replace(key, value)
        subject = subject.replace(key, value)
    try:
        if campaign.channel == "whatsapp":
            response = send_starsender(campaign.connection, contact.phone, body)
        else:
            response = send_mailketing(campaign.connection, contact.email, subject, body)
        recipient.status = "sent"
        recipient.provider_response = response if isinstance(response, dict) else {"raw": str(response)}
        recipient.sent_at = timezone.now()
        recipient.save(update_fields=["status", "provider_response", "sent_at", "updated_at"])
        Campaign.objects.filter(id=campaign.id).update(sent_count=F("sent_count") + 1)
    except Exception as exc:
        if self.request.retries >= 3:
            recipient.status = "failed"
            recipient.error = str(exc)[:2000]
            recipient.save(update_fields=["status", "error", "updated_at"])
            Campaign.objects.filter(id=campaign.id).update(failed_count=F("failed_count") + 1)
        raise
    finally:
        if not CampaignRecipient.objects.filter(campaign=campaign, status="queued").exists():
            Campaign.objects.filter(id=campaign.id).update(status="completed", completed_at=timezone.now())
