from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx
from celery import shared_task
from django.db.models import F
from django.utils import timezone

from crm.models import (
    AutomationRule,
    AutomationRun,
    BackupRecord,
    Campaign,
    CampaignRecipient,
    ChannelConnection,
    Contact,
    Conversation,
    Deal,
    Message,
    PipelineStage,
    Task,
    Tenant,
    WebhookEvent,
)
from crm.services.ai import generate_reply
from crm.services.automations import render_template, trigger_automations
from crm.services.backup import create_database_backup
from crm.services.inbound import apply_delivery_status, store_inbound
from crm.services.messages import extract_provider_message_id
from crm.services.providers import send_mailketing, send_starsender


def _needs_handoff(agent, text: str) -> tuple[bool, str]:
    if not agent:
        return True, "Agent belum dipetakan"
    words = [x.strip().lower() for x in agent.handoff_keywords.split(",") if x.strip()]
    lowered = (text or "").lower()
    for word in words:
        if word and word in lowered:
            return True, f"Kata eskalasi terdeteksi: {word}"
    return False, ""


def _mark_handoff(conversation, reason: str):
    conversation.needs_handoff = True
    conversation.handoff_reason = reason[:250]
    conversation.ai_enabled = False
    conversation.status = "pending"
    conversation.save(
        update_fields=[
            "needs_handoff",
            "handoff_reason",
            "ai_enabled",
            "status",
            "updated_at",
        ]
    )
    trigger_automations(
        "handoff",
        tenant=conversation.tenant,
        brand=conversation.brand,
        contact=conversation.contact,
        conversation=conversation,
        payload={"reason": reason},
        event_key=f"handoff:{conversation.id}:{hashlib.sha256(reason.encode()).hexdigest()[:12]}",
    )


@shared_task(bind=True, max_retries=4)
def process_starsender_event(self, event_id: str):
    event = WebhookEvent.objects.select_related(
        "connection",
        "connection__agent",
        "connection__brand",
        "connection__tenant",
    ).get(id=event_id)
    event.attempts += 1
    event.status = "processing"
    event.error = ""
    event.save(update_fields=["attempts", "status", "error", "updated_at"])

    try:
        if apply_delivery_status(event.connection, event.payload):
            event.status = "processed"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at", "updated_at"])
            return

        conversation, inbound, parsed = store_inbound(event.connection, event.payload)
        if not conversation or not inbound:
            event.status = "ignored"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at", "updated_at"])
            return

        # Prevent duplicate AI output when the same webhook is retried.
        if Message.objects.filter(
            tenant=conversation.tenant,
            conversation=conversation,
            ai_metadata__event_id=str(event.id),
        ).exists():
            event.status = "processed"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at", "updated_at"])
            return

        if parsed.get("contact_created"):
            trigger_automations(
                "new_contact",
                tenant=conversation.tenant,
                brand=conversation.brand,
                contact=conversation.contact,
                conversation=conversation,
                event_key=f"new-contact:{conversation.contact_id}",
            )
        trigger_automations(
            "inbound_message",
            tenant=conversation.tenant,
            brand=conversation.brand,
            contact=conversation.contact,
            conversation=conversation,
            payload={
                "message_id": str(inbound.id),
                "body": inbound.body,
                "provider_message_id": inbound.provider_message_id,
            },
            event_key=f"inbound:{inbound.id}",
        )

        if parsed.get("optout"):
            conversation.ai_enabled = False
            conversation.save(update_fields=["ai_enabled", "updated_at"])
            confirmation = Message.objects.create(
                tenant=conversation.tenant,
                conversation=conversation,
                direction="outbound",
                sender_type="system",
                body="Baik, Anda tidak akan menerima pesan promosi lagi. Pesan layanan yang Anda minta tetap dapat kami bantu.",
                status="queued",
                ai_metadata={"event_id": str(event.id), "optout_confirmation": True},
            )
            send_whatsapp_message.delay(str(confirmation.id))
            event.status = "processed"
            event.processed_at = timezone.now()
            event.save(update_fields=["status", "processed_at", "updated_at"])
            return

        agent = conversation.agent
        handoff, reason = _needs_handoff(agent, inbound.body)
        if handoff:
            _mark_handoff(conversation, reason)
        elif conversation.ai_enabled and agent and agent.is_active:
            reply, metadata = generate_reply(conversation, inbound.body)
            metadata = {**metadata, "event_id": str(event.id)}
            confidence = int(metadata.get("confidence", 0) or 0)
            approval_required = agent.mode in ["draft", "approval", "human"]
            low_confidence = confidence < agent.confidence_threshold

            if approval_required or low_confidence:
                reason = (
                    "Draft AI menunggu persetujuan"
                    if approval_required
                    else f"Confidence AI {confidence}% di bawah batas {agent.confidence_threshold}%"
                )
                Message.objects.create(
                    tenant=conversation.tenant,
                    conversation=conversation,
                    direction="internal",
                    sender_type="ai",
                    body=reply,
                    status="queued",
                    ai_metadata={**metadata, "draft": True},
                )
                _mark_handoff(conversation, reason)
            else:
                message = Message.objects.create(
                    tenant=conversation.tenant,
                    conversation=conversation,
                    direction="outbound",
                    sender_type="ai",
                    body=reply,
                    status="queued",
                    ai_metadata=metadata,
                )
                conversation.last_message_at = timezone.now()
                conversation.save(update_fields=["last_message_at", "updated_at"])
                send_whatsapp_message.delay(str(message.id))

        event.status = "processed"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at", "updated_at"])
    except Exception as exc:
        event.error = str(exc)[:4000]
        event.status = "failed" if self.request.retries >= self.max_retries else "retrying"
        event.save(update_fields=["status", "error", "updated_at"])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(15 * (2 ** self.request.retries), 300))
        # Final failure becomes a human handoff rather than silently losing the chat.
        try:
            if "conversation" in locals() and conversation:
                _mark_handoff(conversation, f"AI/provider error: {str(exc)[:180]}")
        except Exception:
            pass


@shared_task(bind=True, max_retries=3)
def send_whatsapp_message(self, message_id: str):
    message = Message.objects.select_related(
        "conversation__connection",
        "conversation__contact",
        "conversation__tenant",
    ).get(id=message_id)
    if message.status in ["sent", "delivered", "read"]:
        return
    connection = message.conversation.connection
    if not connection or not connection.is_active:
        message.status = "failed"
        message.raw_payload = {**(message.raw_payload or {}), "error": "Koneksi StarSender tidak aktif"}
        message.save(update_fields=["status", "raw_payload", "updated_at"])
        return

    attempts = int((message.ai_metadata or {}).get("send_attempts", 0)) + 1
    metadata = {**(message.ai_metadata or {}), "send_attempts": attempts}
    try:
        result = send_starsender(
            connection,
            message.conversation.contact.phone,
            message.body,
            message.attachment_url,
        )
        provider_id = extract_provider_message_id(result)
        message.status = "sent"
        message.raw_payload = result if isinstance(result, dict) else {"raw": str(result)}
        message.provider_message_id = provider_id or message.provider_message_id
        message.ai_metadata = {**metadata, "last_error": ""}
        message.save(
            update_fields=[
                "status",
                "raw_payload",
                "provider_message_id",
                "ai_metadata",
                "updated_at",
            ]
        )
    except Exception as exc:
        metadata["last_error"] = str(exc)[:2000]
        message.ai_metadata = metadata
        message.raw_payload = {**(message.raw_payload or {}), "error": str(exc)[:2000]}
        if self.request.retries >= self.max_retries:
            message.status = "failed"
            message.save(update_fields=["status", "raw_payload", "ai_metadata", "updated_at"])
            return
        message.status = "queued"
        message.save(update_fields=["status", "raw_payload", "ai_metadata", "updated_at"])
        raise self.retry(exc=exc, countdown=min(10 * (2 ** self.request.retries), 120))


@shared_task
def launch_campaign(campaign_id: str):
    campaign = Campaign.objects.select_related("connection", "brand").get(id=campaign_id)
    if campaign.status == "completed":
        return
    campaign.status = "running"
    campaign.started_at = timezone.now()
    campaign.save(update_fields=["status", "started_at", "updated_at"])
    contacts = Contact.objects.filter(
        tenant=campaign.tenant,
        brand=campaign.brand,
        marketing_consent=True,
    ).exclude(tags__contains=["opt-out"])
    if campaign.channel == "whatsapp":
        contacts = contacts.exclude(phone="")
    else:
        contacts = contacts.exclude(email="")
    if campaign.tag_filter:
        contacts = contacts.filter(tags__contains=[campaign.tag_filter])
    max_recipients = int(getattr(campaign, "max_recipients", 0) or 0) or 500
    contacts = list(contacts[:max_recipients])
    if not contacts:
        campaign.status = "completed"
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["status", "completed_at", "updated_at"])
        return
    delay_seconds = max(1, int((campaign.connection.settings or {}).get("campaign_delay_seconds", 3)))
    for index, contact in enumerate(contacts):
        recipient, _ = CampaignRecipient.objects.get_or_create(
            tenant=campaign.tenant,
            campaign=campaign,
            contact=contact,
        )
        if recipient.status != "sent":
            send_campaign_recipient.apply_async(
                args=[str(recipient.id)],
                countdown=min(index * delay_seconds, 3600),
            )


@shared_task(bind=True, max_retries=3)
def send_campaign_recipient(self, recipient_id: str):
    recipient = CampaignRecipient.objects.select_related(
        "campaign__connection", "contact", "campaign"
    ).get(id=recipient_id)
    if recipient.status == "sent":
        return
    campaign = recipient.campaign
    contact = recipient.contact
    if not contact.marketing_consent or "opt-out" in (contact.tags or []):
        recipient.status = "skipped"
        recipient.error = "Kontak tidak memiliki consent atau sudah opt-out"
        recipient.save(update_fields=["status", "error", "updated_at"])
        return

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
        if self.request.retries >= self.max_retries:
            recipient.status = "failed"
            recipient.error = str(exc)[:2000]
            recipient.save(update_fields=["status", "error", "updated_at"])
            Campaign.objects.filter(id=campaign.id).update(failed_count=F("failed_count") + 1)
            return
        raise self.retry(exc=exc, countdown=min(15 * (2 ** self.request.retries), 300))
    finally:
        if not CampaignRecipient.objects.filter(campaign=campaign, status="queued").exists():
            Campaign.objects.filter(id=campaign.id).update(
                status="completed", completed_at=timezone.now()
            )


@shared_task
def run_due_campaigns():
    due = Campaign.objects.filter(
        status="queued",
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    )[:100]
    for campaign in due:
        launch_campaign.delay(str(campaign.id))


@shared_task(bind=True, max_retries=2)
def execute_automation_run(self, run_id: str):
    run = AutomationRun.objects.select_related(
        "rule",
        "contact",
        "conversation__connection",
        "conversation__contact",
        "deal",
    ).get(id=run_id)
    if run.status == "completed":
        return
    if run.scheduled_for > timezone.now():
        countdown = int((run.scheduled_for - timezone.now()).total_seconds())
        raise self.retry(countdown=max(1, countdown))

    rule = run.rule
    config = rule.config or {}
    run.status = "running"
    run.save(update_fields=["status", "updated_at"])
    try:
        result = {}
        if rule.action == "tag_contact":
            if not run.contact:
                raise RuntimeError("Automation tidak memiliki kontak")
            tag = str(config.get("tag", "")).strip()
            tags = list(run.contact.tags or [])
            if tag and tag not in tags:
                tags.append(tag)
                run.contact.tags = tags
                run.contact.save(update_fields=["tags", "updated_at"])
            result = {"tag": tag}

        elif rule.action == "create_task":
            due_minutes = int(config.get("due_minutes", 0) or 0)
            task = Task.objects.create(
                tenant=run.tenant,
                contact=run.contact,
                deal=run.deal,
                assigned_to=getattr(run.contact, "owner", None),
                title=render_template(
                    config.get("title", rule.name),
                    contact=run.contact,
                    conversation=run.conversation,
                    deal=run.deal,
                ),
                description=render_template(
                    config.get("description", ""),
                    contact=run.contact,
                    conversation=run.conversation,
                    deal=run.deal,
                ),
                due_at=timezone.now() + timedelta(minutes=due_minutes) if due_minutes else None,
                priority=str(config.get("priority", "normal")),
            )
            result = {"task_id": str(task.id)}

        elif rule.action == "send_whatsapp":
            if not run.conversation or not run.conversation.connection:
                raise RuntimeError("Percakapan/StarSender tidak tersedia")
            body = render_template(
                config.get("message", ""),
                contact=run.contact,
                conversation=run.conversation,
                deal=run.deal,
            )
            message = Message.objects.create(
                tenant=run.tenant,
                conversation=run.conversation,
                direction="outbound",
                sender_type="automation",
                body=body,
                status="queued",
                ai_metadata={"automation_run_id": str(run.id)},
            )
            send_whatsapp_message.delay(str(message.id))
            result = {"message_id": str(message.id)}

        elif rule.action == "call_webhook":
            url = str(config.get("url", "")).strip()
            if not url.startswith("https://") and not url.startswith("http://"):
                raise RuntimeError("URL webhook tidak valid")
            body = {
                "event": rule.trigger,
                "rule": rule.name,
                "tenant_id": str(run.tenant_id),
                "contact_id": str(run.contact_id or ""),
                "conversation_id": str(run.conversation_id or ""),
                "deal_id": str(run.deal_id or ""),
                "payload": run.trigger_payload,
            }
            with httpx.Client(timeout=30) as client:
                response = client.post(url, json=body)
            if response.is_error:
                raise RuntimeError(f"Webhook HTTP {response.status_code}: {response.text[:500]}")
            result = {"status_code": response.status_code, "body": response.text[:1000]}

        elif rule.action in ["enable_ai", "disable_ai"]:
            if not run.conversation:
                raise RuntimeError("Automation tidak memiliki percakapan")
            run.conversation.ai_enabled = rule.action == "enable_ai"
            run.conversation.save(update_fields=["ai_enabled", "updated_at"])
            result = {"ai_enabled": run.conversation.ai_enabled}

        elif rule.action == "move_stage":
            if not run.deal:
                raise RuntimeError("Automation tidak memiliki deal")
            stage = PipelineStage.objects.get(
                id=config.get("stage_id"),
                tenant=run.tenant,
                pipeline=run.deal.pipeline,
            )
            run.deal.stage = stage
            run.deal.save(update_fields=["stage", "updated_at"])
            result = {"stage_id": str(stage.id), "stage": stage.name}

        else:
            raise RuntimeError(f"Action automation tidak didukung: {rule.action}")

        run.status = "completed"
        run.result = result
        run.executed_at = timezone.now()
        run.save(update_fields=["status", "result", "executed_at", "updated_at"])
        rule.last_run_at = timezone.now()
        rule.save(update_fields=["last_run_at", "updated_at"])
    except Exception as exc:
        run.error = str(exc)[:4000]
        if self.request.retries >= self.max_retries:
            run.status = "failed"
            run.executed_at = timezone.now()
            run.save(update_fields=["status", "error", "executed_at", "updated_at"])
            return
        run.status = "queued"
        run.save(update_fields=["status", "error", "updated_at"])
        raise self.retry(exc=exc, countdown=min(30 * (2 ** self.request.retries), 300))


@shared_task
def scan_no_reply_automations():
    now = timezone.now()
    rules = AutomationRule.objects.filter(trigger="no_reply", is_active=True).select_related("brand", "tenant")
    for rule in rules:
        minutes = max(1, int((rule.config or {}).get("minutes", 60)))
        cutoff = now - timedelta(minutes=minutes)
        conversations = Conversation.objects.filter(
            tenant=rule.tenant,
            status__in=["open", "pending"],
            last_message_at__lte=cutoff,
        ).select_related("contact", "brand")
        if rule.brand_id:
            conversations = conversations.filter(brand=rule.brand)
        for conversation in conversations[:500]:
            last_key = conversation.last_message_at.replace(second=0, microsecond=0).isoformat()
            unique_key = hashlib.sha256(
                f"{rule.id}:no-reply:{conversation.id}:{last_key}".encode()
            ).hexdigest()
            run, created = AutomationRun.objects.get_or_create(
                tenant=rule.tenant,
                rule=rule,
                unique_key=unique_key,
                defaults={
                    "contact": conversation.contact,
                    "conversation": conversation,
                    "trigger_payload": {"minutes": minutes},
                    "scheduled_for": now + timedelta(minutes=rule.delay_minutes or 0),
                },
            )
            if created:
                execute_automation_run.delay(str(run.id))


@shared_task
def create_scheduled_backup():
    tenants = list(Tenant.objects.filter(is_active=True))
    records = [BackupRecord.objects.create(tenant=tenant, status="running") for tenant in tenants]
    try:
        result = create_database_backup()
        for record in records:
            record.status = "completed"
            record.filename = result["filename"]
            record.size_bytes = result["size_bytes"]
            record.checksum_sha256 = result["checksum_sha256"]
            record.completed_at = timezone.now()
            record.save(
                update_fields=[
                    "status",
                    "filename",
                    "size_bytes",
                    "checksum_sha256",
                    "completed_at",
                    "updated_at",
                ]
            )
    except Exception as exc:
        for record in records:
            record.status = "failed"
            record.error = str(exc)[:4000]
            record.completed_at = timezone.now()
            record.save(update_fields=["status", "error", "completed_at", "updated_at"])
        raise
