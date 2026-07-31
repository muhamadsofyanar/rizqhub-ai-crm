from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from crm.models import (
    AppNotification,
    AutomationRule,
    AutomationRun,
    BackupRecord,
    Broadcast,
    BroadcastRecipient,
    Campaign,
    CampaignRecipient,
    Contact,
    Conversation,
    Message,
    PipelineStage,
    StarSenderAccount,
    StarSenderDevice,
    StarSenderInboundEvent,
    Task,
    Tenant,
    WebhookEvent,
)
from crm.services.ai import generate_reply
from crm.services.automations import render_template, trigger_automations
from crm.services.backup import create_database_backup
from crm.services.features import feature_enabled
from crm.services.handoff import explicit_handoff_reason, get_policy, is_small_talk, set_state
from crm.services.inbound import apply_delivery_status, parse_starsender, store_inbound
from crm.services.memory import update_contact_memory
from crm.services.messages import extract_provider_message_id
from crm.services.providers import ProviderError, send_mailketing, send_starsender
from crm.services.starsender import (
    StarSenderError,
    ensure_device_connection,
    send_group,
    send_personal,
    sync_devices,
    sync_groups,
)


def _notify(tenant, title: str, message: str, *, level: str = "warning", link: str = "", dedupe_key: str = ""):
    if dedupe_key and AppNotification.objects.filter(
        tenant=tenant, dedupe_key=dedupe_key, is_read=False
    ).exists():
        return
    AppNotification.objects.create(
        tenant=tenant,
        level=level,
        title=title[:180],
        message=message[:4000],
        link=link[:500],
        dedupe_key=dedupe_key[:250],
    )


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
    set_state(conversation, "waiting_human")
    if feature_enabled(conversation.tenant, "automation", False):
        trigger_automations(
            "handoff",
            tenant=conversation.tenant,
            brand=conversation.brand,
            contact=conversation.contact,
            conversation=conversation,
            payload={"reason": reason},
            event_key=f"handoff:{conversation.id}:{hashlib.sha256(reason.encode()).hexdigest()[:12]}",
        )
    _notify(
        conversation.tenant,
        "Percakapan menunggu staf",
        f"{conversation.contact}: {reason}",
        level="warning",
        link=f"/inbox/{conversation.id}/",
        dedupe_key=f"handoff:{conversation.id}:{reason[:80]}",
    )


def _create_ai_message(conversation, body: str, metadata: dict, *, internal: bool = False):
    return Message.objects.create(
        tenant=conversation.tenant,
        conversation=conversation,
        direction="internal" if internal else "outbound",
        sender_type="ai",
        body=body,
        status="queued",
        ai_metadata=metadata,
    )


def _process_inbound(connection, payload: dict, event_marker: str, *, device: StarSenderDevice | None = None):
    if apply_delivery_status(connection, payload):
        return "delivery"

    conversation, inbound, parsed = store_inbound(connection, payload, device=device)
    if not conversation or not inbound:
        return "ignored"
    # Dedupe by provider message id, not only by webhook event id. This also
    # protects against the same StarSender message reaching both a legacy
    # device webhook and the consolidated account webhook.
    if parsed.get("duplicate"):
        return "duplicate"

    if Message.objects.filter(
        tenant=conversation.tenant,
        conversation=conversation,
        ai_metadata__event_id=event_marker,
    ).exists():
        return "duplicate"

    if feature_enabled(conversation.tenant, "automation", False):
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
                "chat_type": parsed.get("chat_type"),
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
            body=(
                "Baik, Anda tidak akan menerima pesan promosi lagi. "
                "Pesan layanan yang Anda minta tetap dapat kami bantu."
            ),
            status="queued",
            ai_metadata={"event_id": event_marker, "optout_confirmation": True},
        )
        send_whatsapp_message.delay(str(confirmation.id))
        return "optout"

    if feature_enabled(conversation.tenant, "customer_memory", True) and not parsed.get("is_group"):
        update_contact_memory(conversation.contact, inbound.body)

    agent = conversation.agent
    if not agent:
        _mark_handoff(conversation, "Agent belum dipetakan ke device/brand")
        return "handoff"

    if parsed.get("is_group"):
        group_mode = parsed.get("group_ai_mode") or "off"
        if group_mode == "off":
            return "group_ai_off"
        if group_mode == "mention" and not parsed.get("is_mentioned"):
            return "group_not_mentioned"
        if not conversation.ai_enabled:
            return "group_ai_disabled"

    smart_handoff = feature_enabled(conversation.tenant, "smart_handoff", True)
    if smart_handoff:
        reason = explicit_handoff_reason(agent, inbound.body)
    else:
        normalized = (inbound.body or "").lower()
        matched = next(
            (
                keyword.strip()
                for keyword in (agent.handoff_keywords or "").split(",")
                if keyword.strip() and keyword.strip().lower() in normalized
            ),
            "",
        )
        reason = f"Kata handoff terdeteksi: {matched}" if matched else ""
    if reason:
        _mark_handoff(conversation, reason)
        return "handoff"

    if not conversation.ai_enabled or not agent.is_active:
        return "ai_disabled"

    policy = get_policy(agent)
    # Benign operational test messages never become a handoff.
    if is_small_talk(inbound.body) and "tes" in (inbound.body or "").lower():
        reply = "Pesan tes diterima. Inbox dan AI aktif dengan baik."
        metadata = {"event_id": event_marker, "confidence": 100, "canned": "test"}
    else:
        reply, metadata = generate_reply(conversation, inbound.body)
        metadata = {**metadata, "event_id": event_marker}

    confidence = int(metadata.get("confidence", 0) or 0)
    set_state(conversation, "ai_active", confidence=confidence)
    approval_required = agent.mode in ["draft", "approval", "human"]
    group_draft = parsed.get("is_group") and parsed.get("group_ai_mode") == "draft"

    if approval_required or group_draft:
        _create_ai_message(conversation, reply, {**metadata, "draft": True}, internal=True)
        conversation.needs_handoff = True
        conversation.handoff_reason = "Draft AI menunggu persetujuan"
        conversation.status = "pending"
        conversation.save(update_fields=["needs_handoff", "handoff_reason", "status", "updated_at"])
        set_state(conversation, "waiting_human", confidence=confidence)
        return "draft"

    if not smart_handoff and confidence < agent.confidence_threshold:
        _create_ai_message(conversation, reply, {**metadata, "draft": True}, internal=True)
        _mark_handoff(
            conversation,
            f"Confidence AI {confidence}% di bawah batas agent {agent.confidence_threshold}%",
        )
        return "legacy_low_confidence_handoff"

    if smart_handoff and confidence < policy.clarification_threshold:
        if policy.auto_handoff_low_confidence:
            _create_ai_message(conversation, reply, {**metadata, "draft": True}, internal=True)
            _mark_handoff(
                conversation,
                f"Confidence AI {confidence}% di bawah batas klarifikasi {policy.clarification_threshold}%",
            )
            return "low_confidence_handoff"
        reply = policy.safe_clarification_text
        metadata = {**metadata, "clarification": True}
        set_state(conversation, "clarification", confidence=confidence)
    elif smart_handoff and confidence < policy.reply_threshold:
        metadata = {**metadata, "clarification_recommended": True}
        set_state(conversation, "clarification", confidence=confidence)

    message = _create_ai_message(conversation, reply, metadata)
    conversation.last_message_at = timezone.now()
    conversation.needs_handoff = False
    conversation.handoff_reason = ""
    conversation.status = "open"
    conversation.save(
        update_fields=[
            "last_message_at",
            "needs_handoff",
            "handoff_reason",
            "status",
            "updated_at",
        ]
    )
    send_whatsapp_message.apply_async(
        args=[str(message.id)],
        countdown=max(0, int(policy.reply_delay_seconds or 0)),
    )
    return "ai_queued"


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
        result = _process_inbound(event.connection, event.payload, str(event.id))
        event.status = "ignored" if result in {"ignored", "group_ai_off", "group_not_mentioned"} else "processed"
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at", "updated_at"])
    except Exception as exc:
        event.error = str(exc)[:4000]
        event.status = "failed" if self.request.retries >= self.max_retries else "retrying"
        event.save(update_fields=["status", "error", "updated_at"])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(15 * (2 ** self.request.retries), 300))
        _notify(
            event.connection.tenant,
            "Webhook StarSender gagal diproses",
            str(exc),
            level="error",
            dedupe_key=f"legacy-event:{event.id}",
        )


@shared_task(bind=True, max_retries=4)
def process_starsender_account_event(self, event_id: str):
    event = StarSenderInboundEvent.objects.select_related("account", "account__tenant", "device").get(id=event_id)
    event.attempts += 1
    event.status = "processing"
    event.error = ""
    event.save(update_fields=["attempts", "status", "error", "updated_at"])
    try:
        parsed = parse_starsender(event.payload)
        device = event.device
        if not device and parsed.get("device"):
            device = StarSenderDevice.objects.filter(
                account=event.account,
                external_device_id=parsed["device"],
            ).first()
        if not device:
            try:
                sync_devices(event.account)
            except Exception:
                pass
            device = StarSenderDevice.objects.filter(
                account=event.account,
                external_device_id=parsed.get("device", ""),
            ).first()
        if not device:
            event.status = "needs_mapping"
            event.error = f"Device {parsed.get('device') or parsed.get('device_name') or 'unknown'} belum ditemukan"
            event.save(update_fields=["status", "error", "updated_at"])
            _notify(
                event.tenant,
                "Device StarSender belum dipetakan",
                event.error,
                level="warning",
                link="/starsender/",
                dedupe_key=f"device-missing:{parsed.get('device')}",
            )
            return
        event.device = device
        if not device.brand_id or not device.agent_id:
            event.status = "needs_mapping"
            event.error = "Device ditemukan tetapi Brand/Agent belum dipetakan"
            event.save(update_fields=["device", "status", "error", "updated_at"])
            _notify(
                event.tenant,
                "Mapping device belum lengkap",
                f"{device}: pilih Brand dan AI Agent.",
                level="warning",
                link=f"/starsender/devices/{device.id}/",
                dedupe_key=f"device-map:{device.id}",
            )
            return
        connection = ensure_device_connection(device)
        if not connection:
            raise RuntimeError("Connection device tidak dapat dibuat")
        result = _process_inbound(connection, event.payload, str(event.id), device=device)
        event.status = "ignored" if result in {"ignored", "group_ai_off", "group_not_mentioned"} else "processed"
        event.processed_at = timezone.now()
        event.save(update_fields=["device", "status", "processed_at", "updated_at"])
    except Exception as exc:
        event.error = str(exc)[:4000]
        event.status = "failed" if self.request.retries >= self.max_retries else "retrying"
        event.save(update_fields=["status", "error", "updated_at"])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(15 * (2 ** self.request.retries), 300))
        _notify(
            event.tenant,
            "Webhook multi-device gagal diproses",
            str(exc),
            level="error",
            link="/starsender/",
            dedupe_key=f"account-event:{event.id}",
        )


@shared_task(bind=True, max_retries=3)
def send_whatsapp_message(self, message_id: str):
    """Send one inbox message with an atomic provider-call claim.

    Celery can deliver a task more than once. The ``sending`` state prevents a
    duplicate task from calling StarSender concurrently. Ambiguous network
    failures become ``uncertain`` and are never retried automatically because
    the provider may already have accepted the message.
    """
    notify_payload = None
    with transaction.atomic():
        message = (
            Message.objects.select_for_update()
            .select_related(
                "conversation__connection",
                "conversation__contact",
                "conversation__tenant",
            )
            .get(id=message_id)
        )
        if message.status in {"sent", "delivered", "read", "uncertain"}:
            return
        if message.status == "sending":
            # A live duplicate task must exit. Stale sends are reconciled by the
            # periodic recovery task so they are not resent accidentally.
            return
        if message.status not in {"queued", "failed"}:
            return

        conversation = message.conversation
        connection = conversation.connection
        if not connection or not connection.is_active:
            error = "Koneksi StarSender tidak aktif"
            message.status = "failed"
            message.raw_payload = {**(message.raw_payload or {}), "error": error}
            message.save(update_fields=["status", "raw_payload", "updated_at"])
            notify_payload = (
                conversation.tenant,
                "Pesan WhatsApp gagal",
                f"{conversation.contact}: {error}",
                f"/inbox/{conversation.id}/",
                f"message-failed:{message.id}",
            )
        else:
            attempts = int((message.ai_metadata or {}).get("send_attempts", 0)) + 1
            message.status = "sending"
            message.ai_metadata = {
                **(message.ai_metadata or {}),
                "send_attempts": attempts,
                "send_claimed_at": timezone.now().isoformat(),
                "send_task_id": str(self.request.id or ""),
            }
            message.save(update_fields=["status", "ai_metadata", "updated_at"])

    if notify_payload:
        tenant, title, detail, link, dedupe_key = notify_payload
        _notify(
            tenant,
            title,
            detail,
            level="error",
            link=link,
            dedupe_key=dedupe_key,
        )
        return

    # Provider I/O deliberately happens outside the database transaction.
    provider_accepted = False
    try:
        try:
            device = connection.starsender_device
        except Exception:
            device = None
        if device:
            if conversation.channel == "whatsapp_group":
                result = send_group(
                    device,
                    conversation.external_thread_id,
                    message.body,
                    message.attachment_url,
                )
            else:
                result = send_personal(
                    device,
                    conversation.contact.phone,
                    message.body,
                    message.attachment_url,
                )
        else:
            result = send_starsender(
                connection,
                conversation.contact.phone,
                message.body,
                message.attachment_url,
            )

        provider_accepted = True
        provider_id = extract_provider_message_id(result)
        with transaction.atomic():
            current = Message.objects.select_for_update().get(id=message_id)
            # Delivery/read webhooks can win the race. Never downgrade them.
            if current.status not in {"delivered", "read"}:
                current.status = "sent"
            current.raw_payload = result if isinstance(result, dict) else {"raw": str(result)}
            current.provider_message_id = provider_id or current.provider_message_id
            current.ai_metadata = {
                **(current.ai_metadata or {}),
                "last_error": "",
                "send_completed_at": timezone.now().isoformat(),
            }
            current.save(
                update_fields=[
                    "status",
                    "raw_payload",
                    "provider_message_id",
                    "ai_metadata",
                    "updated_at",
                ]
            )
        return
    except Exception as exc:
        uncertain = provider_accepted or bool(getattr(exc, "uncertain", False))
        retryable = bool(getattr(exc, "retryable", False))
        should_retry = retryable and not uncertain and self.request.retries < self.max_retries
        terminal_status = "uncertain" if uncertain else "failed"

        with transaction.atomic():
            current = (
                Message.objects.select_for_update()
                .select_related("conversation__contact", "conversation__tenant")
                .get(id=message_id)
            )
            # A provider webhook may have confirmed delivery while the request
            # handler was raising. Preserve a confirmed state.
            if current.status in {"sent", "delivered", "read"}:
                return
            metadata = {
                **(current.ai_metadata or {}),
                "last_error": str(exc)[:2000],
                "send_failed_at": timezone.now().isoformat(),
            }
            current.ai_metadata = metadata
            current.raw_payload = {
                **(current.raw_payload or {}),
                "error": str(exc)[:2000],
                "uncertain": uncertain,
            }
            current.status = "queued" if should_retry else terminal_status
            current.save(update_fields=["status", "raw_payload", "ai_metadata", "updated_at"])
            tenant = current.conversation.tenant
            contact_label = str(current.conversation.contact)
            conversation_id = current.conversation_id

        if should_retry:
            raise self.retry(
                exc=exc,
                countdown=min(10 * (2 ** self.request.retries), 120),
            )

        title = "Status pengiriman WhatsApp tidak pasti" if uncertain else "Pesan WhatsApp gagal"
        detail = (
            f"{contact_label}: provider mungkin sudah menerima pesan. "
            f"Jangan kirim ulang sebelum memeriksa WhatsApp/StarSender. Error: {exc}"
            if uncertain
            else f"{contact_label}: {exc}"
        )
        _notify(
            tenant,
            title,
            detail,
            level="error",
            link=f"/inbox/{conversation_id}/",
            dedupe_key=f"message-{terminal_status}:{message_id}",
        )


@shared_task
def sync_all_starsender_devices():
    for account in StarSenderAccount.objects.filter(is_active=True, auto_sync_devices=True).select_related("tenant"):
        if not feature_enabled(account.tenant, "starsender_multi_device", True):
            continue
        try:
            sync_devices(account)
        except Exception as exc:
            account.last_sync_at = timezone.now()
            account.last_sync_status = "failed"
            account.last_error = str(exc)[:4000]
            account.save(update_fields=["last_sync_at", "last_sync_status", "last_error", "updated_at"])
            _notify(
                account.tenant,
                "Sinkronisasi device StarSender gagal",
                f"{account.name}: {exc}",
                level="error",
                link="/starsender/",
                dedupe_key=f"account-sync:{account.id}",
            )


@shared_task
def sync_all_starsender_groups():
    devices = StarSenderDevice.objects.filter(
        account__is_active=True,
        group_sync_enabled=True,
    ).exclude(encrypted_device_key="").select_related("tenant", "account")
    for device in devices:
        if not feature_enabled(device.tenant, "starsender_multi_device", True):
            continue
        try:
            sync_groups(device)
        except Exception as exc:
            device.last_error = str(exc)[:4000]
            device.save(update_fields=["last_error", "updated_at"])
            _notify(
                device.tenant,
                "Sinkronisasi grup StarSender gagal",
                f"{device}: {exc}",
                level="warning",
                link=f"/starsender/devices/{device.id}/groups/",
                dedupe_key=f"group-sync:{device.id}",
            )


@shared_task
def launch_broadcast(broadcast_id: str):
    """Claim and dispatch one broadcast exactly once.

    A row lock prevents double-clicks, repeated Celery delivery, or scheduler
    overlap from scheduling the same recipients twice.
    """
    with transaction.atomic():
        broadcast = (
            Broadcast.objects.select_for_update()
            .select_related("device", "tenant")
            .get(id=broadcast_id)
        )
        feature_key = "group_broadcast" if broadcast.target_type == "group" else "personal_broadcast"
        if not feature_enabled(broadcast.tenant, feature_key, False):
            broadcast.status = "failed"
            broadcast.metadata = {
                **(broadcast.metadata or {}),
                "error": f"Feature {feature_key} belum aktif",
            }
            broadcast.save(update_fields=["status", "metadata", "updated_at"])
            return
        if broadcast.status in {"running", "completed", "cancelled"}:
            return
        max_recipients = int(getattr(settings, "CAMPAIGN_MAX_RECIPIENTS", 500))
        total = broadcast.recipients.count()
        if total == 0:
            broadcast.status = "completed"
            broadcast.total_count = 0
            broadcast.completed_at = timezone.now()
            broadcast.save(
                update_fields=["status", "total_count", "completed_at", "updated_at"]
            )
            return
        if total > max_recipients:
            broadcast.status = "failed"
            broadcast.metadata = {
                **(broadcast.metadata or {}),
                "error": f"Jumlah penerima {total} melebihi batas {max_recipients}",
            }
            broadcast.save(update_fields=["status", "metadata", "updated_at"])
            return
        if not broadcast.device.send_enabled:
            broadcast.status = "failed"
            broadcast.metadata = {
                **(broadcast.metadata or {}),
                "error": "Device pengiriman dinonaktifkan",
            }
            broadcast.save(update_fields=["status", "metadata", "updated_at"])
            return
        recipients = list(broadcast.recipients.filter(status="queued")[:max_recipients])
        if not recipients:
            broadcast.status = "completed"
            broadcast.completed_at = timezone.now()
            broadcast.metadata = {
                **(broadcast.metadata or {}),
                "completion_reason": "Tidak ada penerima berstatus queued; tujuan gagal/tidak pasti tidak dikirim ulang otomatis.",
            }
            broadcast.save(
                update_fields=["status", "completed_at", "metadata", "updated_at"]
            )
            return
        broadcast.status = "running"
        broadcast.started_at = timezone.now()
        broadcast.total_count = total
        broadcast.metadata = {
            **(broadcast.metadata or {}),
            "dispatch_started_at": timezone.now().isoformat(),
        }
        broadcast.save(
            update_fields=["status", "started_at", "total_count", "metadata", "updated_at"]
        )

    delay = max(30, int(broadcast.delay_seconds or 30))
    for index, recipient in enumerate(recipients):
        send_broadcast_recipient.apply_async(
            args=[str(recipient.id)],
            countdown=min(index * delay, 24 * 3600),
        )


@shared_task(bind=True, max_retries=2)
def send_broadcast_recipient(self, recipient_id: str):
    """Send one broadcast target without unsafe automatic duplicate retries."""
    with transaction.atomic():
        # Lock only concrete rows. PostgreSQL rejects ``FOR UPDATE`` when the
        # same query contains nullable OUTER JOINs (contact/group are nullable).
        # Fetch and lock the recipient and broadcast separately to preserve
        # idempotency without locking nullable joined tables.
        recipient = BroadcastRecipient.objects.select_for_update().get(id=recipient_id)
        broadcast = (
            Broadcast.objects.select_for_update()
            .select_related("device", "tenant")
            .get(id=recipient.broadcast_id)
        )
        if recipient.status in {"sending", "sent", "uncertain", "cancelled", "skipped"}:
            return
        if broadcast.status == "cancelled":
            recipient.status = "cancelled"
            recipient.save(update_fields=["status", "updated_at"])
            return
        if recipient.status not in {"queued", "failed"}:
            return
        recipient.status = "sending"
        recipient.attempts += 1
        recipient.error = ""
        recipient.provider_response = {
            **(recipient.provider_response or {}),
            "send_claimed_at": timezone.now().isoformat(),
            "send_task_id": str(self.request.id or ""),
        }
        recipient.save(
            update_fields=["status", "attempts", "error", "provider_response", "updated_at"]
        )

    provider_accepted = False
    try:
        body = broadcast.body
        if recipient.contact:
            replacements = {
                "{{name}}": recipient.contact.name or "Pelanggan",
                "{{phone}}": recipient.contact.phone or "",
                "{{email}}": recipient.contact.email or "",
                "{{company}}": recipient.contact.company or "",
            }
            for key, value in replacements.items():
                body = body.replace(key, value)

        skip_reason = ""
        if broadcast.target_type == "group":
            if not recipient.group or recipient.group.is_locked or not recipient.group.is_active:
                skip_reason = "Grup tidak aktif atau dikunci"
            elif recipient.group.device_id != broadcast.device_id:
                skip_reason = "Grup tidak berasal dari device pengiriman"
            if skip_reason:
                with transaction.atomic():
                    current = BroadcastRecipient.objects.select_for_update().get(id=recipient_id)
                    if current.status == "sending":
                        current.status = "skipped"
                        current.error = skip_reason
                        current.save(update_fields=["status", "error", "updated_at"])
                        Broadcast.objects.filter(id=broadcast.id).update(
                            skipped_count=F("skipped_count") + 1
                        )
                return
            result = send_group(
                broadcast.device,
                recipient.external_target,
                body,
                broadcast.file_url,
            )
        else:
            tags = (
                {str(tag).strip().lower() for tag in (recipient.contact.tags or [])}
                if recipient.contact
                else set()
            )
            if recipient.contact and (
                not recipient.contact.marketing_consent
                or "opt-out" in tags
                or "unsubscribe" in tags
            ):
                skip_reason = "Kontak tidak memiliki consent atau sudah opt-out"
            if skip_reason:
                with transaction.atomic():
                    current = BroadcastRecipient.objects.select_for_update().get(id=recipient_id)
                    if current.status == "sending":
                        current.status = "skipped"
                        current.error = skip_reason
                        current.save(update_fields=["status", "error", "updated_at"])
                        Broadcast.objects.filter(id=broadcast.id).update(
                            skipped_count=F("skipped_count") + 1
                        )
                return
            result = send_personal(
                broadcast.device,
                recipient.external_target,
                body,
                broadcast.file_url,
            )

        provider_accepted = True
        with transaction.atomic():
            current = BroadcastRecipient.objects.select_for_update().get(id=recipient_id)
            if current.status != "sending":
                return
            current.status = "sent"
            current.provider_response = result if isinstance(result, dict) else {"raw": str(result)}
            current.provider_message_id = extract_provider_message_id(result)
            current.sent_at = timezone.now()
            current.error = ""
            current.save(
                update_fields=[
                    "status",
                    "provider_response",
                    "provider_message_id",
                    "sent_at",
                    "error",
                    "updated_at",
                ]
            )
            Broadcast.objects.filter(id=broadcast.id).update(sent_count=F("sent_count") + 1)
    except Exception as exc:
        uncertain = provider_accepted or bool(getattr(exc, "uncertain", False))
        retryable = bool(getattr(exc, "retryable", False))
        should_retry = retryable and not uncertain and self.request.retries < self.max_retries
        terminal_status = "uncertain" if uncertain else "failed"

        with transaction.atomic():
            current = BroadcastRecipient.objects.select_for_update().get(id=recipient_id)
            if current.status == "sent":
                return
            if current.status != "sending":
                return
            current.error = str(exc)[:4000]
            current.provider_response = {
                **(current.provider_response or {}),
                "error": str(exc)[:2000],
                "uncertain": uncertain,
                "send_failed_at": timezone.now().isoformat(),
            }
            current.status = "queued" if should_retry else terminal_status
            current.save(
                update_fields=["status", "error", "provider_response", "updated_at"]
            )
            if not should_retry:
                Broadcast.objects.filter(id=broadcast.id).update(
                    failed_count=F("failed_count") + 1
                )

        if should_retry:
            raise self.retry(
                exc=exc,
                countdown=min(30 * (2 ** self.request.retries), 300),
            )

        title = "Status tujuan broadcast tidak pasti" if uncertain else "Tujuan broadcast gagal"
        detail = (
            f"{recipient.display_name or recipient.external_target}: provider mungkin sudah "
            f"menerima pesan. Jangan kirim ulang sebelum verifikasi. Error: {exc}"
            if uncertain
            else f"{recipient.display_name or recipient.external_target}: {exc}"
        )
        _notify(
            broadcast.tenant,
            title,
            detail,
            level="error",
            link=f"/broadcasts/{broadcast.id}/",
            dedupe_key=f"broadcast-recipient-{terminal_status}:{recipient.id}",
        )
    finally:
        pending = BroadcastRecipient.objects.filter(
            broadcast=broadcast, status__in=["queued", "sending"]
        ).exists()
        if not pending:
            current_status = (
                Broadcast.objects.filter(id=broadcast.id)
                .values_list("status", flat=True)
                .first()
            )
            status = "cancelled" if current_status == "cancelled" else "completed"
            Broadcast.objects.filter(id=broadcast.id).update(
                status=status,
                completed_at=timezone.now(),
            )


@shared_task
def run_due_broadcasts():
    due = Broadcast.objects.filter(
        status="scheduled",
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    )[:100]
    for broadcast in due:
        # launch_broadcast owns the row lock and state transition.
        launch_broadcast.delay(str(broadcast.id))


@shared_task
def recover_stale_provider_sends():
    """Quarantine provider calls whose worker died after claiming the row.

    We intentionally mark these sends as ``uncertain`` rather than retrying.
    The external provider may already have delivered them, so an automatic
    retry could duplicate a personal or group message.
    """
    cutoff = timezone.now() - timedelta(minutes=15)

    stale_message_ids = list(
        Message.objects.filter(status="sending", updated_at__lte=cutoff)
        .values_list("id", flat=True)[:200]
    )
    for message_id in stale_message_ids:
        with transaction.atomic():
            message = (
                Message.objects.select_for_update()
                .select_related("conversation__tenant", "conversation__contact")
                .filter(id=message_id, status="sending", updated_at__lte=cutoff)
                .first()
            )
            if not message:
                continue
            error = (
                "Worker berhenti saat menunggu respons provider. Status pengiriman tidak pasti; "
                "jangan kirim ulang sebelum memeriksa WhatsApp/StarSender."
            )
            message.status = "uncertain"
            message.raw_payload = {
                **(message.raw_payload or {}),
                "error": error,
                "uncertain": True,
                "recovered_at": timezone.now().isoformat(),
            }
            message.save(update_fields=["status", "raw_payload", "updated_at"])
            tenant = message.conversation.tenant
            contact_label = str(message.conversation.contact)
            conversation_id = message.conversation_id
        _notify(
            tenant,
            "Status pengiriman WhatsApp tidak pasti",
            f"{contact_label}: {error}",
            level="error",
            link=f"/inbox/{conversation_id}/",
            dedupe_key=f"message-uncertain:{message_id}",
        )

    stale_recipient_ids = list(
        BroadcastRecipient.objects.filter(status="sending", updated_at__lte=cutoff)
        .values_list("id", flat=True)[:500]
    )
    affected_broadcasts = set()
    for recipient_id in stale_recipient_ids:
        with transaction.atomic():
            recipient = (
                BroadcastRecipient.objects.select_for_update()
                .select_related("broadcast__tenant")
                .filter(id=recipient_id, status="sending", updated_at__lte=cutoff)
                .first()
            )
            if not recipient:
                continue
            error = (
                "Worker berhenti saat menunggu respons provider. Status tujuan tidak pasti; "
                "jangan kirim ulang sebelum verifikasi."
            )
            recipient.status = "uncertain"
            recipient.error = error
            recipient.provider_response = {
                **(recipient.provider_response or {}),
                "error": error,
                "uncertain": True,
                "recovered_at": timezone.now().isoformat(),
            }
            recipient.save(
                update_fields=["status", "error", "provider_response", "updated_at"]
            )
            Broadcast.objects.filter(id=recipient.broadcast_id).update(
                failed_count=F("failed_count") + 1
            )
            affected_broadcasts.add(recipient.broadcast_id)
            tenant = recipient.broadcast.tenant
            display_name = recipient.display_name or recipient.external_target
            broadcast_id = recipient.broadcast_id
        _notify(
            tenant,
            "Status tujuan broadcast tidak pasti",
            f"{display_name}: {error}",
            level="error",
            link=f"/broadcasts/{broadcast_id}/",
            dedupe_key=f"broadcast-recipient-uncertain:{recipient_id}",
        )

    for broadcast_id in affected_broadcasts:
        pending = BroadcastRecipient.objects.filter(
            broadcast_id=broadcast_id,
            status__in=["queued", "sending"],
        ).exists()
        if not pending:
            Broadcast.objects.filter(id=broadcast_id, status="running").update(
                status="completed",
                completed_at=timezone.now(),
            )


# Legacy campaign remains available behind a database feature flag.
@shared_task
def launch_campaign(campaign_id: str):
    campaign = Campaign.objects.select_related("connection", "brand").get(id=campaign_id)
    if not feature_enabled(campaign.tenant, "campaign", False):
        campaign.status = "failed"
        campaign.save(update_fields=["status", "updated_at"])
        return
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
    contacts = list(contacts[:500])
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
                args=[str(recipient.id)], countdown=min(index * delay_seconds, 3600)
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
        status="queued", scheduled_at__isnull=False, scheduled_at__lte=timezone.now()
    )[:100]
    for campaign in due:
        launch_campaign.delay(str(campaign.id))


@shared_task(bind=True, max_retries=2)
def execute_automation_run(self, run_id: str):
    run = AutomationRun.objects.select_related(
        "rule", "contact", "conversation__connection", "conversation__contact", "deal"
    ).get(id=run_id)
    if not feature_enabled(run.tenant, "automation", False):
        run.status = "skipped"
        run.error = "Feature automation belum aktif"
        run.save(update_fields=["status", "error", "updated_at"])
        return
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
                id=config.get("stage_id"), tenant=run.tenant, pipeline=run.deal.pipeline
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
        if not feature_enabled(rule.tenant, "automation", False):
            continue
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
    tenants = [tenant for tenant in Tenant.objects.filter(is_active=True) if feature_enabled(tenant, "backup", True)]
    records = [BackupRecord.objects.create(tenant=tenant, status="running") for tenant in tenants]
    if not records:
        return
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
