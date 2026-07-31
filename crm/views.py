from __future__ import annotations

import hashlib
import json
import mimetypes
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.db import connection as db_connection
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AIReviewForm,
    AgentForm,
    AgentRuntimePolicyForm,
    AgentTestForm,
    AutomationRuleForm,
    BroadcastForm,
    BroadcastTemplateForm,
    CampaignForm,
    GroupCategoryForm,
    GroupPresetForm,
    ConnectionForm,
    ContactForm,
    ConversationAssignmentForm,
    DealStageForm,
    DealQuickUpdateForm,
    InternalNoteForm,
    StarSenderAccountForm,
    StarSenderDeviceForm,
    KnowledgeEntryForm,
    MemberInviteForm,
    ReplyForm,
    WhatsAppGroupForm,
)
from .models import (
    AIReview,
    Agent,
    AgentRuntimePolicy,
    AppNotification,
    AuditLog,
    AutomationRule,
    AutomationRun,
    Broadcast,
    BroadcastTemplate,
    BroadcastRecipient,
    BackupRecord,
    Campaign,
    ChannelConnection,
    Contact,
    FeatureFlag,
    GroupCategory,
    GroupPreset,
    GroupPresetMember,
    Conversation,
    Deal,
    KnowledgeEntry,
    KnowledgeRevision,
    Membership,
    Message,
    Pipeline,
    StarSenderAccount,
    StarSenderDevice,
    StarSenderInboundEvent,
    Subscription,
    Task,
    Tenant,
    UsageRecord,
    WhatsAppGroup,
    WebhookEvent,
)
from .services.ai import generate_reply
from .services.features import DEFAULT_FEATURES, ensure_default_flags, feature_enabled
from .services.handoff import get_control, set_state
from .services.inbound import parse_starsender
from .services.audit import log_audit
from .services.automations import trigger_automations
from .services.starsender import (
    sync_devices,
    sync_groups,
    test_account,
    test_device,
)
from .services.webhook_security import validate_webhook_request
from .tasks import (
    launch_broadcast,
    launch_campaign,
    process_starsender_account_event,
    process_starsender_event,
    send_whatsapp_message,
)


class AppLoginView(LoginView):
    template_name = "registration/login.html"


class AppLogoutView(LogoutView):
    pass


def tenant_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        if not request.tenant:
            return render(request, "crm/no_tenant.html", status=403)
        return view(request, *args, **kwargs)

    return wrapped


def roles_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @tenant_required
        def wrapped(request, *args, **kwargs):
            role = getattr(request.membership, "role", None)
            if role not in allowed_roles:
                messages.error(request, "Anda tidak memiliki izin untuk tindakan ini.")
                return redirect("dashboard")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def health(request):
    checks = {"database": False, "redis": False}
    try:
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["database"] = cursor.fetchone()[0] == 1
    except Exception:
        pass
    try:
        cache.set("healthcheck", "ok", timeout=10)
        checks["redis"] = cache.get("healthcheck") == "ok"
    except Exception:
        pass
    ok = all(checks.values())
    return JsonResponse(
        {"status": "ok" if ok else "degraded", "service": "rizqhub-ai-crm", "checks": checks},
        status=200 if ok else 503,
    )


def _knowledge_revision(entry, user):
    last_number = (
        entry.revisions.order_by("-revision_number")
        .values_list("revision_number", flat=True)
        .first()
        or 0
    )
    KnowledgeRevision.objects.create(
        tenant=entry.tenant,
        entry=entry,
        revision_number=last_number + 1,
        title=entry.title,
        category=entry.category,
        content=entry.content,
        source_url=entry.source_url,
        is_active=entry.is_active,
        changed_by=user,
    )


@tenant_required
def dashboard(request):
    tenant = request.tenant
    contacts = Contact.objects.filter(tenant=tenant)
    conversations = Conversation.objects.filter(tenant=tenant)
    deals = Deal.objects.filter(tenant=tenant)
    context = {
        "contact_count": contacts.count(),
        "new_contacts": contacts.filter(created_at__date=timezone.localdate()).count(),
        "open_conversations": conversations.filter(status="open").count(),
        "handoff_count": conversations.filter(
            needs_handoff=True, status__in=["open", "pending"]
        ).count(),
        "open_deals": deals.filter(status="open").count(),
        "pipeline_value": deals.filter(status="open").aggregate(total=Sum("value"))["total"]
        or Decimal("0"),
        "failed_messages": Message.objects.filter(
            tenant=tenant, status__in=["failed", "uncertain"]
        ).count(),
        "active_automations": AutomationRule.objects.filter(tenant=tenant, is_active=True).count(),
        "recent_conversations": conversations.select_related("contact", "brand", "agent")[:8],
        "recent_tasks": Task.objects.filter(tenant=tenant)
        .exclude(status="done")
        .select_related("contact", "assigned_to")[:8],
        "agents": Agent.objects.filter(tenant=tenant)
        .annotate(conversation_count=Count("conversations"))[:8],
    }
    setup_checks = [
        {
            "key": "starsender",
            "label": "Akun StarSender terhubung",
            "done": StarSenderAccount.objects.filter(tenant=tenant, is_active=True).exists(),
            "url": reverse("starsender_center"),
        },
        {
            "key": "device",
            "label": "Device WhatsApp dipetakan",
            "done": StarSenderDevice.objects.filter(
                tenant=tenant, send_enabled=True, brand__isnull=False, agent__isnull=False
            ).exists(),
            "url": reverse("starsender_center"),
        },
        {
            "key": "agent",
            "label": "AI Agent aktif",
            "done": Agent.objects.filter(tenant=tenant, is_active=True).exists(),
            "url": reverse("agent_list"),
        },
        {
            "key": "knowledge",
            "label": "Pengetahuan bisnis tersedia",
            "done": KnowledgeEntry.objects.filter(tenant=tenant, is_active=True).exists(),
            "url": reverse("knowledge_list"),
        },
        {
            "key": "inbox",
            "label": "Pesan masuk sudah diterima",
            "done": conversations.exists(),
            "url": reverse("inbox"),
        },
    ]
    setup_done = sum(1 for item in setup_checks if item["done"])
    context.update(
        {
            "setup_checks": setup_checks,
            "setup_percent": int((setup_done / len(setup_checks)) * 100),
            "setup_done": setup_done,
            "setup_total": len(setup_checks),
            "knowledge_count": KnowledgeEntry.objects.filter(tenant=tenant, is_active=True).count(),
            "connected_devices": StarSenderDevice.objects.filter(tenant=tenant, connection_status="connected").count(),
        }
    )
    return render(request, "crm/dashboard.html", context)


@tenant_required
def contact_list(request):
    qs = Contact.objects.filter(tenant=request.tenant).select_related("brand", "owner")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(email__icontains=q)
            | Q(company__icontains=q)
        )
    return render(request, "crm/contact_list.html", {"contacts": qs[:300], "q": q})


@tenant_required
def contact_create(request):
    form = ContactForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.owner = request.user
        obj.save()
        log_audit(tenant=request.tenant, action="contact.create", request=request, obj=obj)
        messages.success(request, "Kontak berhasil dibuat.")
        return redirect("contact_detail", pk=obj.pk)
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Tambah kontak",
            "subtitle": "Simpan lead dan pelanggan dalam profil terpadu.",
        },
    )


@tenant_required
def contact_detail(request, pk):
    contact = get_object_or_404(Contact, pk=pk, tenant=request.tenant)
    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact, tenant=request.tenant)
        if form.is_valid():
            form.save()
            log_audit(tenant=request.tenant, action="contact.update", request=request, obj=contact)
            messages.success(request, "Kontak diperbarui.")
            return redirect("contact_detail", pk=contact.pk)
    else:
        form = ContactForm(instance=contact, tenant=request.tenant)
    return render(
        request,
        "crm/contact_detail.html",
        {
            "contact": contact,
            "form": form,
            "conversations": contact.conversations.select_related("agent", "brand")[:10],
            "deals": contact.deals.select_related("stage", "pipeline")[:10],
            "tasks": contact.tasks.select_related("assigned_to")[:10],
        },
    )


@tenant_required
def agent_list(request):
    return render(
        request,
        "crm/agent_list.html",
        {"agents": Agent.objects.filter(tenant=request.tenant).select_related("brand")},
    )


@roles_required("owner", "admin", "manager")
def agent_create(request):
    form = AgentForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        log_audit(tenant=request.tenant, action="agent.create", request=request, obj=obj)
        messages.success(request, "Agent berhasil dibuat.")
        return redirect("agent_edit", pk=obj.pk)
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Buat AI Agent",
            "subtitle": "Atur identitas, guardrail, dan mode operasi agent.",
        },
    )


@roles_required("owner", "admin", "manager")
def agent_edit(request, pk):
    agent = get_object_or_404(Agent, pk=pk, tenant=request.tenant)
    policy, _ = AgentRuntimePolicy.objects.get_or_create(
        tenant=request.tenant,
        agent=agent,
    )
    form = AgentForm(request.POST or None, instance=agent, tenant=request.tenant)
    policy_form = AgentRuntimePolicyForm(
        request.POST or None,
        instance=policy,
        prefix="policy",
    )
    test_form = AgentTestForm()
    test_result = None
    test_meta = None
    action = request.POST.get("action") if request.method == "POST" else ""
    if request.method == "POST" and action == "save" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="agent.update", request=request, obj=agent)
        messages.success(request, "Agent diperbarui.")
        return redirect("agent_edit", pk=agent.pk)
    if request.method == "POST" and action == "save_policy" and policy_form.is_valid():
        policy_form.save()
        log_audit(
            tenant=request.tenant,
            action="agent.policy.update",
            request=request,
            obj=policy,
        )
        messages.success(request, "Smart Handoff dan kontrol respons diperbarui.")
        return redirect("agent_edit", pk=agent.pk)
    if request.method == "POST" and action == "test":
        test_form = AgentTestForm(request.POST)
        if test_form.is_valid():
            contact, _ = Contact.objects.get_or_create(
                tenant=request.tenant,
                phone="TEST-AGENT",
                defaults={"name": "Playground", "brand": agent.brand},
            )
            conversation, _ = Conversation.objects.get_or_create(
                tenant=request.tenant,
                contact=contact,
                brand=agent.brand,
                agent=agent,
                channel="playground",
                defaults={"ai_enabled": False},
            )
            try:
                test_result, test_meta = generate_reply(
                    conversation, test_form.cleaned_data["question"]
                )
            except Exception as exc:
                test_result = f"ERROR: {exc}"
    return render(
        request,
        "crm/agent_edit.html",
        {
            "agent": agent,
            "form": form,
            "policy_form": policy_form,
            "test_form": test_form,
            "test_result": test_result,
            "test_meta": test_meta,
        },
    )


@tenant_required
def knowledge_list(request):
    entries = KnowledgeEntry.objects.filter(tenant=request.tenant).select_related(
        "agent", "agent__brand"
    )
    return render(request, "crm/knowledge_list.html", {"entries": entries})


@roles_required("owner", "admin", "manager")
def knowledge_create(request):
    form = KnowledgeEntryForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        _knowledge_revision(obj, request.user)
        log_audit(tenant=request.tenant, action="knowledge.create", request=request, obj=obj)
        messages.success(request, "Knowledge entry ditambahkan.")
        return redirect("knowledge_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Tambah knowledge",
            "subtitle": "Masukkan FAQ, harga, SOP, dan informasi resmi bisnis.",
        },
    )


@roles_required("owner", "admin", "manager")
def knowledge_edit(request, pk):
    entry = get_object_or_404(KnowledgeEntry, pk=pk, tenant=request.tenant)
    form = KnowledgeEntryForm(
        request.POST or None, instance=entry, tenant=request.tenant
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        _knowledge_revision(entry, request.user)
        log_audit(tenant=request.tenant, action="knowledge.update", request=request, obj=entry)
        messages.success(request, "Knowledge diperbarui dan revisi disimpan.")
        return redirect("knowledge_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Edit knowledge",
            "subtitle": f"Revisi tersimpan: {entry.revisions.count()}",
        },
    )


@roles_required("owner", "admin", "manager")
@require_POST
def knowledge_toggle(request, pk):
    entry = get_object_or_404(KnowledgeEntry, pk=pk, tenant=request.tenant)
    entry.is_active = not entry.is_active
    entry.save(update_fields=["is_active", "updated_at"])
    _knowledge_revision(entry, request.user)
    log_audit(tenant=request.tenant, action="knowledge.toggle", request=request, obj=entry)
    return redirect("knowledge_list")


def _conversation_ui_state(conversation):
    """Return one human-readable state for the operational inbox."""
    if conversation.status == "closed":
        return {"key": "done", "label": "Selesai", "css": "status-done"}
    if conversation.needs_handoff:
        return {"key": "help", "label": "Perlu Admin", "css": "status-help"}
    if conversation.assigned_to_id and not conversation.ai_enabled:
        return {"key": "admin", "label": "Admin Menangani", "css": "status-admin"}
    if conversation.status == "pending":
        return {"key": "wait", "label": "Menunggu Pelanggan", "css": "status-wait"}
    if conversation.ai_enabled:
        return {"key": "ai", "label": "AI Menangani", "css": "status-ai"}
    return {"key": "admin", "label": "Belum Ditangani", "css": "status-admin"}


def _inbox_queryset(request):
    latest_message = Message.objects.filter(conversation=OuterRef("pk")).order_by(
        "-created_at"
    )
    qs = (
        Conversation.objects.filter(tenant=request.tenant)
        .select_related("contact", "brand", "agent", "assigned_to")
        .annotate(
            latest_message_body=Subquery(latest_message.values("body")[:1]),
            latest_sender_type=Subquery(latest_message.values("sender_type")[:1]),
        )
    )
    status = request.GET.get("status", "")
    if status in ["open", "pending", "closed"]:
        qs = qs.filter(status=status)
    if request.GET.get("handoff") == "1":
        qs = qs.filter(needs_handoff=True)
    inbox_filter = request.GET.get("filter", "")
    if inbox_filter == "unread":
        qs = qs.filter(unread_count__gt=0)
    elif inbox_filter == "admin":
        qs = qs.filter(Q(needs_handoff=True) | Q(ai_enabled=False)).exclude(status="closed")
    elif inbox_filter == "ai":
        qs = qs.filter(ai_enabled=True, needs_handoff=False).exclude(status="closed")
    elif inbox_filter == "group":
        qs = qs.filter(channel__in=["group", "whatsapp_group"])
    elif inbox_filter == "personal":
        qs = qs.exclude(channel__in=["group", "whatsapp_group"])
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(contact__name__icontains=q)
            | Q(contact__phone__icontains=q)
            | Q(latest_message_body__icontains=q)
        )
    return qs


@tenant_required
def inbox(request):
    return render(
        request,
        "crm/inbox.html",
        {
            "conversations": _inbox_queryset(request)[:200],
            "selected": None,
            "inbox_filter": request.GET.get("filter", ""),
            "q": request.GET.get("q", ""),
            "live_poll_ms": int(settings.LIVE_INBOX_POLL_SECONDS * 1000),
        },
    )


@tenant_required
@require_GET
def inbox_conversations_api(request):
    rows = list(_inbox_queryset(request)[:200])
    payload = []
    for conversation in rows:
        display_name = str(conversation.contact)
        ui_state = _conversation_ui_state(conversation)
        payload.append(
            {
                "id": str(conversation.id),
                "url": reverse("conversation_detail", args=[conversation.id]),
                "contact_name": display_name,
                "brand_name": conversation.brand.name,
                "agent_name": conversation.agent.name if conversation.agent else "Tanpa agent",
                "last_message_at": timezone.localtime(conversation.last_message_at).isoformat(),
                "last_message_label": timezone.localtime(conversation.last_message_at).strftime(
                    "%d %b %H:%M"
                ),
                "last_message_preview": (conversation.latest_message_body or "")[:120],
                "last_sender_type": conversation.latest_sender_type or "",
                "status": conversation.status,
                "channel": conversation.channel,
                "ai_enabled": conversation.ai_enabled,
                "needs_handoff": conversation.needs_handoff,
                "handoff_reason": conversation.handoff_reason,
                "unread_count": conversation.unread_count,
                "assigned_to": (
                    conversation.assigned_to.get_full_name()
                    or conversation.assigned_to.username
                    if conversation.assigned_to
                    else ""
                ),
                "ui_status": ui_state["label"],
                "ui_status_key": ui_state["key"],
                "ui_status_class": ui_state["css"],
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "conversations": payload,
            "generated_at": timezone.now().isoformat(),
        }
    )


@tenant_required
def conversation_detail(request, pk):
    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "contact", "brand", "agent", "connection", "assigned_to"
        ),
        pk=pk,
        tenant=request.tenant,
    )
    if request.method == "POST":
        form = ReplyForm(request.POST)
        if form.is_valid():
            if not conversation.connection or conversation.connection.provider != "starsender":
                messages.error(request, "Percakapan belum terhubung ke koneksi StarSender.")
            else:
                msg = Message.objects.create(
                    tenant=request.tenant,
                    conversation=conversation,
                    direction="outbound",
                    sender_type="user",
                    sender_user=request.user,
                    body=form.cleaned_data["body"],
                    status="queued",
                )
                conversation.ai_enabled = False
                conversation.needs_handoff = False
                conversation.handoff_reason = ""
                conversation.assigned_to = request.user
                conversation.last_message_at = timezone.now()
                conversation.status = "open"
                conversation.save(
                    update_fields=[
                        "ai_enabled",
                        "needs_handoff",
                        "handoff_reason",
                        "assigned_to",
                        "last_message_at",
                        "status",
                        "updated_at",
                    ]
                )
                set_state(conversation, "human_active")
                send_whatsapp_message.delay(str(msg.id))
                log_audit(
                    tenant=request.tenant,
                    action="message.send.manual",
                    request=request,
                    obj=msg,
                )
                messages.success(request, "Pesan masuk antrean pengiriman.")
                return redirect("conversation_detail", pk=conversation.pk)
    else:
        form = ReplyForm()
    conversation.unread_count = 0
    conversation.save(update_fields=["unread_count", "updated_at"])
    return render(
        request,
        "crm/conversation_detail.html",
        {
            "conversation": conversation,
            "conversation_messages": conversation.messages.select_related("sender_user").prefetch_related("reviews"),
            "reply_form": form,
            "note_form": InternalNoteForm(),
            "assignment_form": ConversationAssignmentForm(
                tenant=request.tenant,
                initial={"assigned_to": conversation.assigned_to},
            ),
            "conversations": _inbox_queryset(request)[:100],
            "selected": conversation,
            "conversation_ui_state": _conversation_ui_state(conversation),
            "inbox_filter": request.GET.get("filter", ""),
            "q": request.GET.get("q", ""),
            "conversation_control": get_control(conversation),
            "contact_memory": getattr(conversation.contact, "memory", None),
            "active_deal": conversation.contact.deals.filter(status="open").select_related("stage", "pipeline").first(),
            "live_poll_ms": int(settings.LIVE_INBOX_POLL_SECONDS * 1000),
        },
    )


@tenant_required
@require_GET
def conversation_messages_api(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
    control = get_control(conversation)
    rows = conversation.messages.select_related("sender_user").prefetch_related("reviews").order_by("created_at")
    limit = min(max(int(request.GET.get("limit", 200)), 20), 500)
    rows = list(rows[max(0, rows.count() - limit) :])
    payload = []
    for item in rows:
        review = item.reviews.filter(reviewer=request.user).first()
        payload.append(
            {
                "id": str(item.id),
                "direction": item.direction,
                "sender_type": item.sender_type,
                "body": item.body,
                "message_type": item.message_type,
                "attachment_url": item.attachment_url,
                "status": item.status,
                "created_at": timezone.localtime(item.created_at).isoformat(),
                "created_label": timezone.localtime(item.created_at).strftime("%d %b %H:%M"),
                "confidence": (item.ai_metadata or {}).get("confidence"),
                "can_retry": item.direction == "outbound" and item.status == "failed",
                "can_approve": item.direction == "internal"
                and item.sender_type == "ai"
                and not (item.ai_metadata or {}).get("approved_message_id"),
                "review": review.verdict if review else "",
                "retry_url": reverse("message_retry", args=[item.id]),
                "approve_url": reverse("message_approve", args=[item.id]),
                "review_url": reverse("message_review", args=[item.id]),
            }
        )
    conversation.unread_count = 0
    conversation.save(update_fields=["unread_count", "updated_at"])
    return JsonResponse(
        {
            "ok": True,
            "messages": payload,
            "conversation": {
                "status": conversation.status,
                "ui_status": _conversation_ui_state(conversation)["label"],
                "ui_status_key": _conversation_ui_state(conversation)["key"],
                "ui_status_class": _conversation_ui_state(conversation)["css"],
                "last_confidence": control.last_confidence,
                "ai_enabled": conversation.ai_enabled,
                "needs_handoff": conversation.needs_handoff,
                "handoff_reason": conversation.handoff_reason,
                "assigned_to": conversation.assigned_to.get_full_name()
                or conversation.assigned_to.username
                if conversation.assigned_to
                else "",
            },
        }
    )


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def conversation_action(request, pk, action):
    conv = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
    if action == "enable-ai":
        conv.ai_enabled = True
        conv.needs_handoff = False
        conv.handoff_reason = ""
        conv.status = "open"
    elif action == "takeover":
        conv.ai_enabled = False
        conv.needs_handoff = False
        conv.handoff_reason = ""
        conv.assigned_to = request.user
        conv.status = "open"
    elif action == "wait-customer":
        conv.status = "pending"
        conv.needs_handoff = False
        conv.handoff_reason = ""
        conv.ai_enabled = bool(conv.agent_id)
    elif action == "pending":
        conv.status = "pending"
        conv.ai_enabled = False
    elif action == "close":
        conv.status = "closed"
        conv.ai_enabled = False
    elif action == "reopen":
        conv.status = "open"
    else:
        raise Http404
    conv.save()
    if action == "enable-ai":
        set_state(conv, "ai_active")
    elif action == "wait-customer":
        set_state(conv, "ai_active")
    elif action in {"takeover", "pending"}:
        set_state(conv, "human_active" if action == "takeover" else "waiting_human")
    elif action == "close":
        set_state(conv, "resolved")
    log_audit(
        tenant=request.tenant,
        action=f"conversation.{action}",
        request=request,
        obj=conv,
    )
    return redirect("conversation_detail", pk=conv.pk)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def conversation_note(request, pk):
    conv = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
    form = InternalNoteForm(request.POST)
    if form.is_valid():
        note = Message.objects.create(
            tenant=request.tenant,
            conversation=conv,
            direction="internal",
            sender_type="user",
            sender_user=request.user,
            body=form.cleaned_data["note"],
            status="sent",
        )
        log_audit(tenant=request.tenant, action="conversation.note", request=request, obj=note)
    return redirect("conversation_detail", pk=conv.pk)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def conversation_assign(request, pk):
    conv = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
    form = ConversationAssignmentForm(request.POST, tenant=request.tenant)
    if form.is_valid():
        conv.assigned_to = form.cleaned_data["assigned_to"]
        conv.ai_enabled = False if conv.assigned_to else conv.ai_enabled
        conv.save(update_fields=["assigned_to", "ai_enabled", "updated_at"])
        log_audit(
            tenant=request.tenant,
            action="conversation.assign",
            request=request,
            obj=conv,
            metadata={"assigned_to": str(conv.assigned_to_id or "")},
        )
    return redirect("conversation_detail", pk=conv.pk)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def message_retry(request, pk):
    with transaction.atomic():
        msg = get_object_or_404(
            Message.objects.select_for_update(),
            pk=pk,
            tenant=request.tenant,
            direction="outbound",
        )
        should_queue = msg.status == "failed"
        if should_queue:
            metadata = {
                **(msg.ai_metadata or {}),
                "manual_retry_by": request.user.id,
                "manual_retry_at": timezone.now().isoformat(),
            }
            msg.status = "queued"
            msg.ai_metadata = metadata
            msg.save(update_fields=["status", "ai_metadata", "updated_at"])
    if should_queue:
        send_whatsapp_message.delay(str(msg.id))
        log_audit(tenant=request.tenant, action="message.retry", request=request, obj=msg)
    return redirect("conversation_detail", pk=msg.conversation_id)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def message_approve(request, pk):
    with transaction.atomic():
        draft = get_object_or_404(
            Message.objects.select_for_update().select_related("conversation"),
            pk=pk,
            tenant=request.tenant,
            direction="internal",
            sender_type="ai",
        )
        approved_id = (draft.ai_metadata or {}).get("approved_message_id")
        if approved_id:
            return redirect("conversation_detail", pk=draft.conversation_id)
        outbound = Message.objects.create(
            tenant=request.tenant,
            conversation=draft.conversation,
            direction="outbound",
            sender_type="ai-approved",
            sender_user=request.user,
            body=draft.body,
            status="queued",
            ai_metadata={**(draft.ai_metadata or {}), "approved_by": request.user.id},
        )
        draft.ai_metadata = {
            **(draft.ai_metadata or {}),
            "approved_message_id": str(outbound.id),
        }
        draft.save(update_fields=["ai_metadata", "updated_at"])
    send_whatsapp_message.delay(str(outbound.id))
    log_audit(tenant=request.tenant, action="message.approve_ai", request=request, obj=outbound)
    return redirect("conversation_detail", pk=draft.conversation_id)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def message_review(request, pk):
    msg = get_object_or_404(Message, pk=pk, tenant=request.tenant, sender_type__startswith="ai")
    form = AIReviewForm(request.POST)
    if form.is_valid():
        review, _ = AIReview.objects.update_or_create(
            tenant=request.tenant,
            message=msg,
            reviewer=request.user,
            defaults={
                "verdict": form.cleaned_data["verdict"],
                "comment": form.cleaned_data["comment"],
                "corrected_response": form.cleaned_data["corrected_response"],
            },
        )
        log_audit(tenant=request.tenant, action="ai.review", request=request, obj=review)
    return redirect("conversation_detail", pk=msg.conversation_id)


@tenant_required
def pipeline_board(request):
    pipelines = list(
        Pipeline.objects.filter(tenant=request.tenant)
        .select_related("brand")
        .order_by("brand__name", "name")
    )
    selected_id = (request.GET.get("pipeline") or "").strip()
    selected_pipeline = next(
        (pipeline for pipeline in pipelines if str(pipeline.id) == selected_id),
        pipelines[0] if pipelines else None,
    )
    query = (request.GET.get("q") or "").strip()
    stages = []
    board_total = Decimal("0")
    board_open = 0
    board_won = 0
    board_lost = 0
    if selected_pipeline:
        deals = Deal.objects.filter(
            tenant=request.tenant,
            pipeline=selected_pipeline,
        ).select_related("contact", "owner", "stage")
        if query:
            deals = deals.filter(
                Q(title__icontains=query)
                | Q(contact__name__icontains=query)
                | Q(contact__phone__icontains=query)
                | Q(contact__company__icontains=query)
            )
        deals = list(deals.order_by("-updated_at"))
        board_open = sum(1 for deal in deals if deal.status == "open")
        board_won = sum(1 for deal in deals if deal.status == "won")
        board_lost = sum(1 for deal in deals if deal.status == "lost")
        board_total = sum((deal.value for deal in deals if deal.status == "open"), Decimal("0"))
        for stage in selected_pipeline.stages.all():
            stage.visible_deals = [deal for deal in deals if deal.stage_id == stage.id]
            stage.visible_count = len(stage.visible_deals)
            stage.visible_value = sum((deal.value for deal in stage.visible_deals), Decimal("0"))
            stages.append(stage)
    return render(
        request,
        "crm/pipeline.html",
        {
            "pipelines": pipelines,
            "selected_pipeline": selected_pipeline,
            "stages": stages,
            "query": query,
            "board_total": board_total,
            "board_open": board_open,
            "board_won": board_won,
            "board_lost": board_lost,
            "pipeline_users": User.objects.filter(
                memberships__tenant=request.tenant, memberships__is_active=True
            ).distinct().order_by("first_name", "username"),
        },
    )


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def deal_quick_update(request, pk):
    deal = get_object_or_404(Deal, pk=pk, tenant=request.tenant)
    form = DealQuickUpdateForm(request.POST, instance=deal, tenant=request.tenant)
    if form.is_valid():
        previous_status = deal.status
        updated = form.save()
        log_audit(
            tenant=request.tenant,
            action="deal.quick_update",
            request=request,
            obj=updated,
            metadata={"previous_status": previous_status, "status": updated.status},
        )
        messages.success(request, f"Deal {updated.title} diperbarui.")
    else:
        error_text = "; ".join(
            message for messages_list in form.errors.values() for message in messages_list
        )
        messages.error(request, error_text or "Data deal belum valid.")
    pipeline_id = request.POST.get("pipeline") or str(deal.pipeline_id)
    query = (request.POST.get("q") or "").strip()
    target = f"{reverse('pipeline_board')}?pipeline={pipeline_id}"
    if query:
        target += f"&q={query}"
    return redirect(target)


@roles_required("owner", "admin", "manager", "sales", "cs")
@require_POST
def deal_move(request, pk):
    deal = get_object_or_404(Deal, pk=pk, tenant=request.tenant)
    form = DealStageForm(request.POST, deal=deal)
    if form.is_valid():
        old_stage = deal.stage
        deal.stage = form.cleaned_data["stage"]
        deal.save(update_fields=["stage", "updated_at"])
        trigger_automations(
            "deal_stage",
            tenant=request.tenant,
            brand=deal.brand,
            contact=deal.contact,
            deal=deal,
            payload={"old_stage": str(old_stage.id), "stage_id": str(deal.stage_id)},
            event_key=f"deal-stage:{deal.id}:{deal.stage_id}:{timezone.now().isoformat()}",
        )
        log_audit(
            tenant=request.tenant,
            action="deal.move_stage",
            request=request,
            obj=deal,
            metadata={"from": old_stage.name, "to": deal.stage.name},
        )
    pipeline_id = request.POST.get("pipeline") or str(deal.pipeline_id)
    return redirect(f"{reverse('pipeline_board')}?pipeline={pipeline_id}")


@tenant_required
def automation_list(request):
    rules = AutomationRule.objects.filter(tenant=request.tenant).select_related("brand")
    recent_runs = AutomationRun.objects.filter(tenant=request.tenant).select_related("rule")[:25]
    return render(
        request,
        "crm/automation_list.html",
        {"rules": rules, "recent_runs": recent_runs},
    )


@roles_required("owner", "admin", "manager")
def automation_create(request):
    form = AutomationRuleForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.tenant = request.tenant
        rule.save()
        log_audit(tenant=request.tenant, action="automation.create", request=request, obj=rule)
        messages.success(request, "Automation berhasil dibuat.")
        return redirect("automation_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Buat automation",
            "subtitle": "Aktifkan bertahap dan uji pada satu brand terlebih dahulu.",
        },
    )


@roles_required("owner", "admin", "manager")
def automation_edit(request, pk):
    rule = get_object_or_404(AutomationRule, pk=pk, tenant=request.tenant)
    form = AutomationRuleForm(request.POST or None, instance=rule, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="automation.update", request=request, obj=rule)
        messages.success(request, "Automation diperbarui.")
        return redirect("automation_list")
    return render(
        request,
        "crm/form.html",
        {"form": form, "title": "Edit automation", "subtitle": rule.name},
    )


@roles_required("owner", "admin", "manager")
@require_POST
def automation_toggle(request, pk):
    rule = get_object_or_404(AutomationRule, pk=pk, tenant=request.tenant)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active", "updated_at"])
    log_audit(tenant=request.tenant, action="automation.toggle", request=request, obj=rule)
    return redirect("automation_list")


@tenant_required
def ai_evaluation(request):
    reviews = AIReview.objects.filter(tenant=request.tenant).select_related(
        "message__conversation__contact", "reviewer"
    )[:100]
    ai_messages = Message.objects.filter(
        tenant=request.tenant,
        sender_type__startswith="ai",
    )
    metrics = {
        "total": ai_messages.count(),
        "reviewed": AIReview.objects.filter(tenant=request.tenant).values("message_id").distinct().count(),
        "helpful": AIReview.objects.filter(tenant=request.tenant, verdict="helpful").count(),
        "needs_edit": AIReview.objects.filter(tenant=request.tenant, verdict="needs_edit").count(),
        "unsafe": AIReview.objects.filter(tenant=request.tenant, verdict="unsafe").count(),
        "usage": UsageRecord.objects.filter(tenant=request.tenant, kind="ai_response").aggregate(total=Sum("units"))["total"] or 0,
    }
    return render(request, "crm/ai_evaluation.html", {"reviews": reviews, "metrics": metrics})


@tenant_required
def integration_list(request):
    connections = ChannelConnection.objects.filter(tenant=request.tenant).select_related(
        "brand", "agent"
    )
    return render(
        request,
        "crm/integration_list.html",
        {"connections": connections, "app_base_url": settings.APP_BASE_URL},
    )


@roles_required("owner", "admin")
def integration_create(request):
    form = ConnectionForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        log_audit(tenant=request.tenant, action="integration.create", request=request, obj=obj)
        messages.success(request, "Integrasi berhasil disimpan.")
        return redirect("integration_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Tambah integrasi",
            "subtitle": "Hubungkan StarSender atau Mailketing secara aman.",
        },
    )


@roles_required("owner", "admin")
def integration_edit(request, pk):
    connection = get_object_or_404(ChannelConnection, pk=pk, tenant=request.tenant)
    form = ConnectionForm(request.POST or None, instance=connection, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="integration.update", request=request, obj=connection)
        messages.success(request, "Integrasi diperbarui.")
        return redirect("integration_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": "Edit integrasi",
            "subtitle": "API credential disimpan dalam bentuk terenkripsi.",
        },
    )


@roles_required("owner", "admin")
def workspace(request):
    subscription, _ = Subscription.objects.get_or_create(
        tenant=request.tenant,
        defaults={
            "plan": "starter",
            "status": "active",
            "limits": {
                "users": 10,
                "brands": 5,
                "monthly_ai_tokens": 1000000,
                "monthly_campaign_recipients": 5000,
            },
        },
    )
    memberships = Membership.objects.filter(tenant=request.tenant).select_related("user")
    invite_form = MemberInviteForm(request.POST or None)
    if request.method == "POST" and invite_form.is_valid():
        email = invite_form.cleaned_data["email"].lower()
        user = User.objects.filter(Q(username=email) | Q(email__iexact=email)).first()
        created = False
        if not user:
            user = User.objects.create_user(
                username=email,
                email=email,
                password=invite_form.cleaned_data["temporary_password"],
            )
            created = True
        membership, member_created = Membership.objects.get_or_create(
            tenant=request.tenant,
            user=user,
            defaults={"role": invite_form.cleaned_data["role"], "is_active": True},
        )
        if not member_created:
            membership.role = invite_form.cleaned_data["role"]
            membership.is_active = True
            membership.save(update_fields=["role", "is_active", "updated_at"])
        log_audit(
            tenant=request.tenant,
            action="workspace.member.add",
            request=request,
            obj=membership,
            metadata={"created_user": created},
        )
        messages.success(request, "Pengguna berhasil ditambahkan ke workspace.")
        return redirect("workspace")
    return render(
        request,
        "crm/workspace.html",
        {
            "subscription": subscription,
            "memberships": memberships,
            "invite_form": invite_form,
            "brand_count": request.tenant.brand_set.count() if hasattr(request.tenant, "brand_set") else 0,
        },
    )


@roles_required("owner", "admin")
def system_health(request):
    checks = {"database": False, "redis": False}
    try:
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["database"] = cursor.fetchone()[0] == 1
    except Exception:
        pass
    try:
        cache.set("system-health", "ok", timeout=30)
        checks["redis"] = cache.get("system-health") == "ok"
    except Exception:
        pass
    context = {
        "checks": checks,
        "failed_messages": Message.objects.filter(
            tenant=request.tenant, status__in=["failed", "uncertain"]
        )[:50],
        "failed_webhooks": WebhookEvent.objects.filter(tenant=request.tenant, status="failed")[:50],
        "failed_account_webhooks": StarSenderInboundEvent.objects.filter(
            tenant=request.tenant, status__in=["failed", "needs_mapping"]
        )[:50],
        "failed_automations": AutomationRun.objects.filter(tenant=request.tenant, status="failed")[:50],
        "failed_broadcasts": Broadcast.objects.filter(tenant=request.tenant, status="failed")[:30],
        "problem_broadcast_recipients": BroadcastRecipient.objects.filter(
            tenant=request.tenant, status__in=["failed", "uncertain"]
        ).select_related("broadcast")[:50],
        "notifications": AppNotification.objects.filter(tenant=request.tenant, is_read=False)[:50],
        "backups": BackupRecord.objects.filter(tenant=request.tenant)[:30],
        "audit_logs": AuditLog.objects.filter(tenant=request.tenant).select_related("user")[:50],
    }
    return render(request, "crm/system_health.html", context)


@login_required
@require_POST
def switch_tenant(request, tenant_id):
    membership = request.user.memberships.filter(
        tenant_id=tenant_id, is_active=True
    ).first()
    if membership:
        request.session["tenant_id"] = str(membership.tenant_id)
    return redirect(request.META.get("HTTP_REFERER") or "/")


@csrf_exempt
def starsender_webhook(request, token):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)
    blocked = validate_webhook_request(request, token)
    if blocked:
        return blocked
    connection = get_object_or_404(
        ChannelConnection,
        provider="starsender",
        webhook_token=token,
        is_active=True,
    )
    try:
        payload = json.loads(request.body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    digest = hashlib.sha256(request.body).hexdigest()
    event, created = WebhookEvent.objects.get_or_create(
        tenant=connection.tenant,
        provider="starsender",
        payload_hash=digest,
        defaults={
            "connection": connection,
            "external_event_id": str(
                payload.get("message_id") or payload.get("messageId") or payload.get("id") or ""
            ),
            "payload": payload,
        },
    )
    if created:
        process_starsender_event.delay(str(event.id))
    return JsonResponse({"ok": True, "queued": created})


@csrf_exempt
def mailketing_webhook(request, token):
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    blocked = validate_webhook_request(request, token)
    if blocked:
        return blocked
    connection = get_object_or_404(
        ChannelConnection,
        provider="mailketing",
        webhook_token=token,
        is_active=True,
    )
    try:
        payload = json.loads(request.body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    digest = hashlib.sha256(request.body).hexdigest()
    WebhookEvent.objects.get_or_create(
        tenant=connection.tenant,
        provider="mailketing",
        payload_hash=digest,
        defaults={"connection": connection, "payload": payload, "status": "received"},
    )
    email = payload.get("email")
    if email:
        contact = Contact.objects.filter(
            tenant=connection.tenant, email__iexact=email
        ).first()
        if contact:
            contact.custom_fields = {
                **contact.custom_fields,
                "mailketing_last_event": payload.get("type"),
                "mailketing_last_event_at": str(timezone.now()),
            }
            contact.save(update_fields=["custom_fields", "updated_at"])
    return JsonResponse({"ok": True})


@tenant_required
def campaign_list(request):
    campaigns = Campaign.objects.filter(tenant=request.tenant).select_related(
        "brand", "connection"
    )
    return render(request, "crm/campaign_list.html", {"campaigns": campaigns})


@roles_required("owner", "admin", "manager", "marketing")
def campaign_create(request):
    form = CampaignForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.tenant = request.tenant
        campaign.save()
        log_audit(tenant=request.tenant, action="campaign.create", request=request, obj=campaign)
        messages.success(request, "Campaign disimpan sebagai draft. Tinjau sebelum menjalankan.")
        return redirect("campaign_detail", pk=campaign.pk)
    return render(
        request,
        "crm/campaign_form.html",
        {
            "form": form,
            "title": "Buat Campaign Email",
        },
    )


@tenant_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(
        Campaign.objects.select_related("brand", "connection"),
        pk=pk,
        tenant=request.tenant,
    )
    recipients = campaign.recipients.select_related("contact")[:500]
    eligible = Contact.objects.filter(
        tenant=request.tenant,
        brand=campaign.brand,
        marketing_consent=True,
    ).exclude(tags__contains=["opt-out"])
    if campaign.channel == "whatsapp":
        eligible = eligible.exclude(phone="")
    else:
        eligible = eligible.exclude(email="")
    if campaign.tag_filter:
        eligible = eligible.filter(tags__contains=[campaign.tag_filter])
    return render(
        request,
        "crm/campaign_detail.html",
        {
            "campaign": campaign,
            "recipients": recipients,
            "eligible_count": min(eligible.count(), settings.CAMPAIGN_MAX_RECIPIENTS),
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def campaign_start(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, tenant=request.tenant)
    if campaign.status not in ["draft", "failed"]:
        messages.error(request, "Campaign ini sudah dijalankan atau sedang diproses.")
        return redirect("campaign_detail", pk=campaign.pk)
    campaign.status = "queued"
    campaign.save(update_fields=["status", "updated_at"])
    if not campaign.scheduled_at or campaign.scheduled_at <= timezone.now():
        launch_campaign.delay(str(campaign.id))
        messages.success(request, "Campaign masuk antrean pengiriman.")
    else:
        messages.success(
            request,
            f"Campaign dijadwalkan pada {timezone.localtime(campaign.scheduled_at):%d %b %Y %H:%M}.",
        )
    log_audit(tenant=request.tenant, action="campaign.start", request=request, obj=campaign)
    return redirect("campaign_detail", pk=campaign.pk)


@roles_required("owner", "admin")
def feature_settings(request):
    flags = ensure_default_flags(request.tenant)
    return render(
        request,
        "crm/feature_settings.html",
        {"flags": flags, "defaults": DEFAULT_FEATURES},
    )


@roles_required("owner", "admin")
@require_POST
def feature_toggle(request, key):
    ensure_default_flags(request.tenant)
    flag = get_object_or_404(FeatureFlag, tenant=request.tenant, key=key)
    new_value = request.POST.get("enabled") == "true"
    if flag.is_dangerous and new_value and request.POST.get("confirm") != "AKTIFKAN":
        messages.error(request, "Ketik AKTIFKAN untuk menyalakan fitur berisiko.")
        return redirect("feature_settings")
    flag.enabled = new_value
    flag.updated_by = request.user
    flag.save(update_fields=["enabled", "updated_by", "updated_at"])
    log_audit(
        tenant=request.tenant,
        action="feature.toggle",
        request=request,
        obj=flag,
        metadata={"enabled": new_value},
    )
    messages.success(request, f"{flag.label} {'diaktifkan' if new_value else 'dinonaktifkan'} tanpa redeploy.")
    return redirect("feature_settings")


@roles_required("owner", "admin", "manager", "marketing")
def starsender_center(request):
    accounts = StarSenderAccount.objects.filter(tenant=request.tenant).prefetch_related("devices")
    devices = StarSenderDevice.objects.filter(tenant=request.tenant).select_related(
        "account", "brand", "agent", "connection"
    )
    groups = WhatsAppGroup.objects.filter(tenant=request.tenant)
    account_exists = accounts.exists()
    account_tested = accounts.filter(last_sync_status__in=["connection_ok", "success"]).exists()
    devices_exist = devices.exists()
    mapped_devices = devices.filter(
        brand__isnull=False, agent__isnull=False, encrypted_device_key__gt=""
    )
    ready_devices = mapped_devices.filter(send_enabled=True)
    setup_steps = [
        {"number": 1, "label": "Hubungkan akun", "description": "Simpan Account API Key", "done": account_exists, "current": not account_exists},
        {"number": 2, "label": "Uji & sinkronkan", "description": "Ambil seluruh device", "done": account_tested and devices_exist, "current": account_exists and not (account_tested and devices_exist)},
        {"number": 3, "label": "Petakan device", "description": "Pilih Brand, Agent, dan Device Key", "done": devices_exist and mapped_devices.count() == devices.count(), "current": devices_exist and mapped_devices.count() != devices.count()},
        {"number": 4, "label": "Uji pengiriman", "description": "Pastikan device siap mengirim", "done": ready_devices.exists(), "current": mapped_devices.exists() and not ready_devices.exists()},
        {"number": 5, "label": "Sinkronkan grup", "description": "Siapkan kategori dan preset", "done": groups.exists(), "current": ready_devices.exists() and not groups.exists()},
    ]
    return render(
        request,
        "crm/starsender_center.html",
        {
            "accounts": accounts,
            "devices": devices,
            "group_count": groups.count(),
            "connected_count": devices.filter(connection_status="connected").count(),
            "unmapped_count": devices.filter(Q(brand__isnull=True) | Q(agent__isnull=True)).count(),
            "ready_count": ready_devices.count(),
            "setup_steps": setup_steps,
            "app_base_url": settings.APP_BASE_URL,
        },
    )


@roles_required("owner", "admin")
def starsender_account_create(request):
    form = StarSenderAccountForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        account = form.save(commit=False)
        account.tenant = request.tenant
        account.save()
        log_audit(tenant=request.tenant, action="starsender.account.create", request=request, obj=account)
        messages.success(request, "Akun StarSender disimpan. Lanjutkan dengan Uji Koneksi.")
        return redirect("starsender_center")
    return render(
        request,
        "crm/starsender_account_form.html",
        {"form": form, "title": "Hubungkan akun StarSender", "subtitle": "Account API Key dipakai hanya untuk membaca dan menyinkronkan device."},
    )


@roles_required("owner", "admin")
def starsender_account_edit(request, pk):
    account = get_object_or_404(StarSenderAccount, pk=pk, tenant=request.tenant)
    form = StarSenderAccountForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="starsender.account.update", request=request, obj=account)
        messages.success(request, "Akun StarSender diperbarui.")
        return redirect("starsender_center")
    return render(
        request,
        "crm/starsender_account_form.html",
        {"form": form, "title": f"Pengaturan akun — {account.name}", "subtitle": "Perbarui Account API Key tanpa menampilkannya kembali.", "account": account, "webhook_url": f"{settings.APP_BASE_URL}/webhooks/starsender/account/{account.webhook_token}/"},
    )


@roles_required("owner", "admin")
@require_POST
def starsender_account_test(request, pk):
    account = get_object_or_404(StarSenderAccount, pk=pk, tenant=request.tenant)
    try:
        result = sync_devices(account)
        account.refresh_from_db()
        messages.success(
            request,
            f"Account API Key valid. {result['total']} device terbaca dan sudah disinkronkan.",
        )
    except Exception as exc:
        account.last_sync_status = "failed"
        account.last_error = str(exc)[:4000]
        account.save(update_fields=["last_sync_status", "last_error", "updated_at"])
        messages.error(request, f"Uji koneksi gagal: {exc}")
    return redirect("starsender_center")


@roles_required("owner", "admin")
@require_POST
def starsender_account_sync(request, pk):
    account = get_object_or_404(StarSenderAccount, pk=pk, tenant=request.tenant)
    try:
        result = sync_devices(account)
        messages.success(
            request,
            f"Sinkronisasi selesai: {result['created']} device baru, {result['updated']} diperbarui, total {result['total']}.",
        )
    except Exception as exc:
        account.last_sync_status = "failed"
        account.last_error = str(exc)[:4000]
        account.save(update_fields=["last_sync_status", "last_error", "updated_at"])
        messages.error(request, f"Sinkronisasi gagal: {exc}")
    return redirect("starsender_center")


@roles_required("owner", "admin")
def starsender_device_edit(request, pk):
    device = get_object_or_404(StarSenderDevice, pk=pk, tenant=request.tenant)
    form = StarSenderDeviceForm(request.POST or None, instance=device, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="starsender.device.update", request=request, obj=device)
        messages.success(request, "Mapping device, Device Key, dan pengaturan pengiriman disimpan.")
        return redirect("starsender_center")
    return render(
        request,
        "crm/starsender_device_form.html",
        {
            "form": form,
            "device": device,
            "title": f"Siapkan device — {device}",
            "subtitle": "Pilih Brand dan AI Agent yang benar, lalu masukkan Device Key untuk pengiriman.",
        },
    )


@roles_required("owner", "admin")
@require_POST
def starsender_device_test(request, pk):
    device = get_object_or_404(StarSenderDevice, pk=pk, tenant=request.tenant)
    try:
        result = test_device(device)
        device.last_error = ""
        device.save(update_fields=["last_error", "updated_at"])
        messages.success(request, f"Device Key valid: {result.get('message', 'OK')}")
    except Exception as exc:
        device.last_error = str(exc)[:4000]
        device.save(update_fields=["last_error", "updated_at"])
        messages.error(request, f"Uji Device Key gagal: {exc}")
    return redirect("starsender_center")


@roles_required("owner", "admin", "manager", "marketing")
def starsender_device_groups(request, pk):
    device = get_object_or_404(StarSenderDevice, pk=pk, tenant=request.tenant)
    groups = device.whatsapp_groups.prefetch_related("category_links__category")
    return render(
        request,
        "crm/whatsapp_groups.html",
        {"device": device, "groups": groups},
    )


@roles_required("owner", "admin")
@require_POST
def starsender_device_groups_sync(request, pk):
    device = get_object_or_404(StarSenderDevice, pk=pk, tenant=request.tenant)
    try:
        result = sync_groups(device)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, **result})
        if result.get("warning"):
            messages.warning(request, result["warning"])
        else:
            messages.success(
                request,
                f"Grup disinkronkan: {result['created']} baru, {result['updated']} diperbarui, total {result['total']}.",
            )
    except Exception as exc:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Sinkronisasi grup gagal: {exc}")
    return redirect("starsender_device_groups", pk=device.pk)


@roles_required("owner", "admin", "manager", "marketing")
def whatsapp_group_list(request):
    groups = WhatsAppGroup.objects.filter(tenant=request.tenant).select_related(
        "device", "device__brand"
    ).prefetch_related("category_links__category")
    device_id = (request.GET.get("device") or "").strip()
    query = (request.GET.get("q") or "").strip()
    if device_id:
        groups = groups.filter(device_id=device_id)
    if query:
        groups = groups.filter(
            Q(name__icontains=query)
            | Q(external_group_id__icontains=query)
            | Q(category_links__category__name__icontains=query)
        ).distinct()
    category_id = (request.GET.get("category") or "").strip()
    if category_id:
        groups = groups.filter(category_links__category_id=category_id).distinct()
    devices = StarSenderDevice.objects.filter(tenant=request.tenant).order_by("name", "phone_number")
    categories = GroupCategory.objects.filter(tenant=request.tenant, is_active=True).order_by("name")
    return render(
        request,
        "crm/whatsapp_group_list.html",
        {
            "groups": groups,
            "devices": devices,
            "categories": categories,
            "selected_device": device_id,
            "selected_category": category_id,
            "query": query,
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def whatsapp_group_bulk_action(request):
    group_ids = request.POST.getlist("groups")
    groups = WhatsAppGroup.objects.filter(tenant=request.tenant, id__in=group_ids)
    if not group_ids or not groups.exists():
        messages.error(request, "Pilih minimal satu grup.")
        return redirect("whatsapp_group_list")
    action = (request.POST.get("action") or "").strip()
    if action == "category":
        category = get_object_or_404(
            GroupCategory,
            pk=request.POST.get("category"),
            tenant=request.tenant,
            is_active=True,
        )
        added = 0
        for group in groups:
            _, created = group.category_links.get_or_create(
                tenant=request.tenant,
                category=category,
            )
            added += int(created)
        messages.success(request, f"Kategori {category.name} diterapkan. {added} relasi baru dibuat.")
    elif action == "lock":
        groups.update(is_locked=True, updated_at=timezone.now())
        messages.success(request, f"{groups.count()} grup dikunci dari broadcast.")
    elif action == "unlock":
        groups.update(is_locked=False, updated_at=timezone.now())
        messages.success(request, f"{groups.count()} grup diizinkan untuk dipilih kembali.")
    elif action == "activate":
        groups.update(is_active=True, updated_at=timezone.now())
        messages.success(request, f"{groups.count()} grup diaktifkan.")
    elif action == "deactivate":
        groups.update(is_active=False, updated_at=timezone.now())
        messages.success(request, f"{groups.count()} grup dinonaktifkan.")
    else:
        messages.error(request, "Aksi massal tidak dikenali.")
    return redirect("whatsapp_group_list")


@roles_required("owner", "admin", "manager", "marketing")
def whatsapp_group_edit(request, pk):
    group = get_object_or_404(WhatsAppGroup, pk=pk, tenant=request.tenant)
    form = WhatsAppGroupForm(request.POST or None, instance=group, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        ai_enabled = group.ai_mode in {"mention", "draft", "autonomous"}
        group_conversations = Conversation.objects.filter(
            tenant=request.tenant,
            channel="whatsapp_group",
            external_thread_id=group.external_group_id,
            status__in=["open", "pending"],
        )
        if ai_enabled:
            group_conversations.update(
                ai_enabled=True, needs_handoff=False, handoff_reason="", status="open"
            )
        else:
            group_conversations.update(ai_enabled=False)
        log_audit(tenant=request.tenant, action="starsender.group.update", request=request, obj=group)
        messages.success(request, "Kategori, keamanan, dan mode AI grup diperbarui.")
        return redirect("starsender_device_groups", pk=group.device_id)
    return render(
        request,
        "crm/form.html",
        {"form": form, "title": f"Edit grup — {group.name}", "subtitle": "Kunci grup internal agar tidak pernah menjadi target broadcast."},
    )


@roles_required("owner", "admin", "manager", "marketing")
def group_category_create(request):
    form = GroupCategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save(commit=False)
        category.tenant = request.tenant
        category.save()
        log_audit(
            tenant=request.tenant,
            action="starsender.group_category.create",
            request=request,
            obj=category,
        )
        messages.success(request, "Kategori grup dibuat.")
        return redirect("group_preset_list")
    return render(request, "crm/form.html", {"form": form, "title": "Tambah kategori grup", "subtitle": "Kategori dapat dipakai oleh banyak grup dan preset dinamis."})


@roles_required("owner", "admin", "manager", "marketing")
def group_category_edit(request, pk):
    category = get_object_or_404(GroupCategory, pk=pk, tenant=request.tenant)
    form = GroupCategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(
            tenant=request.tenant,
            action="starsender.group_category.update",
            request=request,
            obj=category,
        )
        messages.success(request, "Kategori grup diperbarui.")
        return redirect("group_preset_list")
    return render(
        request,
        "crm/form.html",
        {
            "form": form,
            "title": f"Edit kategori — {category.name}",
            "subtitle": "Nonaktifkan kategori bila tidak lagi digunakan; data preset lama tetap tersimpan.",
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
def group_preset_list(request):
    query = (request.GET.get("q") or "").strip()
    presets = GroupPreset.objects.filter(tenant=request.tenant).select_related("brand", "category")
    categories = GroupCategory.objects.filter(tenant=request.tenant).annotate(
        group_count=Count("group_links", distinct=True)
    )
    if query:
        presets = presets.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
            | Q(brand__name__icontains=query)
        )
        categories = categories.filter(Q(name__icontains=query) | Q(description__icontains=query))
    presets = list(presets)
    for preset in presets:
        if preset.preset_type == "static":
            resolved = WhatsAppGroup.objects.filter(
                preset_links__preset=preset,
                is_active=True,
                is_locked=False,
            ).select_related("device")
        elif preset.category_id:
            resolved = WhatsAppGroup.objects.filter(
                tenant=request.tenant,
                category_links__category=preset.category,
                is_active=True,
                is_locked=False,
            ).select_related("device").distinct()
            if preset.brand_id:
                resolved = resolved.filter(device__brand=preset.brand)
        else:
            resolved = WhatsAppGroup.objects.none()
        preset.resolved_count = resolved.count()
        preset.preview_groups = list(resolved[:5])
    return render(
        request,
        "crm/group_presets.html",
        {"presets": presets, "categories": categories, "query": query},
    )


@roles_required("owner", "admin", "manager", "marketing")
def group_preset_create(request):
    form = GroupPresetForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        preset = form.save(commit=False)
        preset.tenant = request.tenant
        preset.created_by = request.user
        preset.save()
        form.instance = preset
        form.save()
        log_audit(
            tenant=request.tenant,
            action="starsender.group_preset.create",
            request=request,
            obj=preset,
        )
        messages.success(request, "Preset grup dibuat.")
        return redirect("group_preset_list")
    return render(request, "crm/group_preset_form.html", {"form": form, "title": "Buat preset grup"})


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def group_preset_duplicate(request, pk):
    source = get_object_or_404(GroupPreset, pk=pk, tenant=request.tenant)
    base_name = f"Salinan {source.name}"
    name = base_name
    counter = 2
    while GroupPreset.objects.filter(tenant=request.tenant, name=name).exists():
        name = f"{base_name} {counter}"
        counter += 1
    duplicate = GroupPreset.objects.create(
        tenant=request.tenant,
        name=name,
        description=source.description,
        preset_type=source.preset_type,
        brand=source.brand,
        category=source.category,
        is_active=False,
        created_by=request.user,
    )
    for member in source.members.select_related("group"):
        GroupPresetMember.objects.create(
            tenant=request.tenant, preset=duplicate, group=member.group
        )
    log_audit(tenant=request.tenant, action="group_preset.duplicate", request=request, obj=duplicate)
    messages.success(request, "Preset diduplikat dalam kondisi nonaktif. Periksa lalu aktifkan saat sudah benar.")
    return redirect("group_preset_edit", pk=duplicate.pk)


@roles_required("owner", "admin", "manager", "marketing")
def group_preset_edit(request, pk):
    preset = get_object_or_404(GroupPreset, pk=pk, tenant=request.tenant)
    form = GroupPresetForm(request.POST or None, instance=preset, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(
            tenant=request.tenant,
            action="starsender.group_preset.update",
            request=request,
            obj=preset,
        )
        messages.success(request, "Preset grup diperbarui.")
        return redirect("group_preset_list")
    return render(request, "crm/group_preset_form.html", {"form": form, "title": f"Edit preset — {preset.name}", "preset": preset})


def _preset_groups(preset, device):
    if not preset:
        return WhatsAppGroup.objects.none()
    if preset.preset_type == "static":
        return WhatsAppGroup.objects.filter(
            id__in=preset.members.values_list("group_id", flat=True),
            device=device,
            is_active=True,
            is_locked=False,
        )
    qs = WhatsAppGroup.objects.filter(
        device=device,
        is_active=True,
        is_locked=False,
        category_links__category=preset.category,
    )
    if preset.brand_id:
        qs = qs.filter(device__brand=preset.brand)
    return qs.distinct()


@roles_required("owner", "admin", "manager", "marketing")
@require_GET
def broadcast_groups_api(request):
    device_id = request.GET.get("device", "").strip()
    if not device_id:
        return JsonResponse({"ok": True, "groups": [], "presets": []})
    device = get_object_or_404(StarSenderDevice, pk=device_id, tenant=request.tenant)
    groups = WhatsAppGroup.objects.filter(
        tenant=request.tenant,
        device=device,
        is_active=True,
        is_locked=False,
    ).prefetch_related("category_links__category")
    group_rows = [
        {
            "id": str(group.id),
            "name": group.name,
            "external_id": group.external_group_id,
            "categories": [link.category.name for link in group.category_links.all()],
        }
        for group in groups
    ]
    preset_rows = []
    for preset in GroupPreset.objects.filter(tenant=request.tenant, is_active=True).select_related("category"):
        selected = list(_preset_groups(preset, device).values_list("id", flat=True))
        if selected:
            preset_rows.append(
                {
                    "id": str(preset.id),
                    "name": preset.name,
                    "type": preset.preset_type,
                    "group_ids": [str(value) for value in selected],
                    "count": len(selected),
                }
            )
    return JsonResponse(
        {
            "ok": True,
            "device": {"id": str(device.id), "name": str(device)},
            "groups": group_rows,
            "presets": preset_rows,
            "sync_url": reverse("starsender_device_groups_sync", args=[device.id]),
            "manage_url": reverse("starsender_device_groups", args=[device.id]),
        }
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_GET
def broadcast_template_api(request, pk):
    template = get_object_or_404(BroadcastTemplate, pk=pk, tenant=request.tenant, is_active=True)
    media_url = template.media_url
    if not media_url and template.media_file:
        media_url = request.build_absolute_uri(
            reverse("broadcast_template_media", args=[template.public_token])
        )
    return JsonResponse(
        {
            "ok": True,
            "id": str(template.id),
            "name": template.name,
            "message_type": template.message_type,
            "body": template.body,
            "media_url": media_url,
        }
    )


def broadcast_template_media(request, token):
    template = get_object_or_404(BroadcastTemplate, public_token=token, is_active=True)
    if not template.media_file:
        raise Http404("Media tidak tersedia")
    filename = template.media_file.name.rsplit("/", 1)[-1]
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = FileResponse(template.media_file.open("rb"), content_type=content_type)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["Cache-Control"] = "public, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_template_list(request):
    templates = BroadcastTemplate.objects.filter(tenant=request.tenant)
    query = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip()
    message_type = (request.GET.get("type") or "").strip()
    if query:
        templates = templates.filter(Q(name__icontains=query) | Q(body__icontains=query))
    if category:
        templates = templates.filter(category=category)
    if message_type in {"text", "media"}:
        templates = templates.filter(message_type=message_type)
    categories = list(
        BroadcastTemplate.objects.filter(tenant=request.tenant)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )
    return render(
        request,
        "crm/broadcast_templates.html",
        {
            "templates": templates,
            "categories": categories,
            "query": query,
            "selected_category": category,
            "selected_type": message_type,
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def broadcast_template_duplicate(request, pk):
    source = get_object_or_404(BroadcastTemplate, pk=pk, tenant=request.tenant)
    base_name = f"Salinan {source.name}"[:160]
    name = base_name
    counter = 2
    while BroadcastTemplate.objects.filter(tenant=request.tenant, name=name).exists():
        suffix = f" ({counter})"
        name = f"{base_name[:180-len(suffix)]}{suffix}"
        counter += 1
    duplicate = BroadcastTemplate.objects.create(
        tenant=request.tenant,
        name=name,
        category=source.category,
        message_type=source.message_type,
        body=source.body,
        media_file=source.media_file,
        media_url=source.media_url,
        is_active=False,
        created_by=request.user,
    )
    log_audit(
        tenant=request.tenant,
        action="broadcast_template.duplicate",
        request=request,
        obj=duplicate,
        metadata={"source_id": str(source.id)},
    )
    messages.success(request, "Template disalin sebagai draft nonaktif. Periksa lalu aktifkan setelah benar.")
    return redirect("broadcast_template_edit", pk=duplicate.pk)


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_template_create(request):
    form = BroadcastTemplateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        template = form.save(commit=False)
        template.tenant = request.tenant
        template.created_by = request.user
        template.save()
        log_audit(tenant=request.tenant, action="broadcast.template.create", request=request, obj=template)
        messages.success(request, "Template pesan disimpan dan siap digunakan.")
        return redirect("broadcast_template_list")
    return render(request, "crm/broadcast_template_form.html", {"form": form, "title": "Buat template pesan"})


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_template_edit(request, pk):
    template = get_object_or_404(BroadcastTemplate, pk=pk, tenant=request.tenant)
    form = BroadcastTemplateForm(request.POST or None, request.FILES or None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="broadcast.template.update", request=request, obj=template)
        messages.success(request, "Template pesan diperbarui.")
        return redirect("broadcast_template_list")
    return render(request, "crm/broadcast_template_form.html", {"form": form, "title": f"Edit template — {template.name}", "template": template})


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def group_preset_quick_create(request):
    name = (request.POST.get("name") or "").strip()
    device_id = (request.POST.get("device") or "").strip()
    group_ids = request.POST.getlist("groups")
    if not name:
        return JsonResponse({"ok": False, "error": "Nama preset wajib diisi."}, status=400)
    device = get_object_or_404(StarSenderDevice, pk=device_id, tenant=request.tenant)
    groups = list(
        WhatsAppGroup.objects.filter(
            tenant=request.tenant,
            device=device,
            id__in=group_ids,
            is_active=True,
            is_locked=False,
        )
    )
    if not groups:
        return JsonResponse({"ok": False, "error": "Pilih minimal satu grup."}, status=400)
    if GroupPreset.objects.filter(tenant=request.tenant, name__iexact=name).exists():
        return JsonResponse({"ok": False, "error": "Nama preset sudah digunakan."}, status=409)
    preset = GroupPreset.objects.create(
        tenant=request.tenant,
        name=name,
        description=f"Preset cepat untuk {device}",
        preset_type="static",
        brand=device.brand,
        created_by=request.user,
        is_active=True,
    )
    GroupPresetMember.objects.bulk_create(
        [GroupPresetMember(tenant=request.tenant, preset=preset, group=group) for group in groups],
        ignore_conflicts=True,
    )
    log_audit(tenant=request.tenant, action="starsender.group_preset.quick_create", request=request, obj=preset, metadata={"groups": len(groups)})
    return JsonResponse({"ok": True, "id": str(preset.id), "name": preset.name, "count": len(groups)})


@roles_required("owner", "admin")
@require_POST
def starsender_sync_all_groups(request):
    devices = StarSenderDevice.objects.filter(
        tenant=request.tenant,
        group_sync_enabled=True,
    ).exclude(encrypted_device_key="")
    total = created = updated = failed = 0
    errors = []
    for device in devices:
        try:
            result = sync_groups(device)
            total += result["total"]
            created += result["created"]
            updated += result["updated"]
            if result.get("warning"):
                errors.append(f"{device}: {result['warning']}")
        except Exception as exc:
            failed += 1
            errors.append(f"{device}: {exc}")
    if errors:
        messages.warning(request, f"Sinkronisasi selesai dengan {len(errors)} catatan. Total grup terbaca: {total}. " + " | ".join(errors[:3]))
    else:
        messages.success(request, f"Semua grup disinkronkan. {created} baru, {updated} diperbarui, total {total}.")
    return redirect("starsender_center")


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_list(request):
    broadcasts = Broadcast.objects.filter(tenant=request.tenant).select_related(
        "device", "preset", "created_by"
    )
    query = (request.GET.get("q") or "").strip()
    target = (request.GET.get("target") or "").strip()
    status = (request.GET.get("status") or "").strip()
    if query:
        broadcasts = broadcasts.filter(
            Q(name__icontains=query)
            | Q(body__icontains=query)
            | Q(device__name__icontains=query)
            | Q(preset__name__icontains=query)
        )
    if target in {"personal", "group"}:
        broadcasts = broadcasts.filter(target_type=target)
    valid_statuses = {choice[0] for choice in Broadcast.STATUS_CHOICES}
    if status in valid_statuses:
        broadcasts = broadcasts.filter(status=status)
    all_broadcasts = Broadcast.objects.filter(tenant=request.tenant)
    stats = {
        "draft": all_broadcasts.filter(status="draft").count(),
        "active": all_broadcasts.filter(status__in=["scheduled", "queued", "running"]).count(),
        "completed": all_broadcasts.filter(status="completed").count(),
        "failed": all_broadcasts.filter(status="failed").count(),
        "sent": all_broadcasts.aggregate(total=Sum("sent_count"))["total"] or 0,
    }
    return render(
        request,
        "crm/broadcast_list.html",
        {
            "broadcasts": broadcasts[:300],
            "stats": stats,
            "query": query,
            "selected_target": target,
            "selected_status": status,
            "device_count": StarSenderDevice.objects.filter(
                tenant=request.tenant, send_enabled=True
            ).count(),
            "group_count": WhatsAppGroup.objects.filter(
                tenant=request.tenant, is_active=True, is_locked=False
            ).count(),
            "template_count": BroadcastTemplate.objects.filter(
                tenant=request.tenant, is_active=True
            ).count(),
            "preset_count": GroupPreset.objects.filter(
                tenant=request.tenant, is_active=True
            ).count(),
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_create(request):
    initial = {}
    target_hint = (request.GET.get("target") or "").strip()
    if target_hint in {"personal", "group"}:
        initial["target_type"] = target_hint
    form = BroadcastForm(request.POST or None, tenant=request.tenant, initial=initial)
    if request.method == "POST" and form.is_valid():
        broadcast = form.save(commit=False)
        broadcast.tenant = request.tenant
        broadcast.created_by = request.user
        broadcast.status = "draft"
        selected_template = form.cleaned_data.get("template")
        if selected_template:
            # Body remains editable. Use the template only when the field was left blank.
            if not (broadcast.body or "").strip():
                broadcast.body = selected_template.body
            if broadcast.message_type == "media" and not broadcast.file_url:
                if selected_template.media_url:
                    broadcast.file_url = selected_template.media_url
                elif selected_template.media_file:
                    broadcast.file_url = request.build_absolute_uri(
                        reverse("broadcast_template_media", args=[selected_template.public_token])
                    )
            BroadcastTemplate.objects.filter(pk=selected_template.pk).update(
                usage_count=selected_template.usage_count + 1,
                last_used_at=timezone.now(),
            )
        broadcast.metadata = {
            "consent_confirmed": bool(form.cleaned_data.get("confirm_consent")),
            "group_permission_confirmed": bool(form.cleaned_data.get("confirm_group_permission")),
        }
        broadcast.save()
        recipient_count = 0
        if broadcast.target_type == "personal":
            selected_targets = set()
            for contact in form.cleaned_data.get("contacts", []):
                key = hashlib.sha256(f"{broadcast.id}:contact:{contact.id}".encode()).hexdigest()
                normalized_phone = "".join(ch for ch in (contact.phone or "") if ch.isdigit())
                if normalized_phone.startswith("0"):
                    normalized_phone = "62" + normalized_phone[1:]
                if not normalized_phone or normalized_phone in selected_targets:
                    continue
                selected_targets.add(normalized_phone)
                BroadcastRecipient.objects.get_or_create(
                    tenant=request.tenant,
                    broadcast=broadcast,
                    idempotency_key=key,
                    defaults={
                        "contact": contact,
                        "external_target": normalized_phone,
                        "display_name": contact.name or contact.phone,
                        "status": "queued",
                    },
                )
                recipient_count += 1
            manual_numbers = {
                "".join(ch for ch in line if ch.isdigit())
                for line in (form.cleaned_data.get("manual_numbers") or "").splitlines()
                if line.strip()
            }
            for number in sorted(n for n in manual_numbers if n):
                if number.startswith("0"):
                    number = "62" + number[1:]
                if number in selected_targets:
                    continue
                selected_targets.add(number)
                key = hashlib.sha256(f"{broadcast.id}:number:{number}".encode()).hexdigest()
                _, created = BroadcastRecipient.objects.get_or_create(
                    tenant=request.tenant,
                    broadcast=broadcast,
                    idempotency_key=key,
                    defaults={
                        "external_target": number,
                        "display_name": number,
                        "status": "queued",
                    },
                )
                recipient_count += int(created)
        else:
            groups = list(form.cleaned_data.get("groups", []))
            preset = form.cleaned_data.get("preset")
            if preset:
                groups.extend(list(_preset_groups(preset, broadcast.device)))
            unique_groups = {str(group.id): group for group in groups}
            for group in unique_groups.values():
                if group.device_id != broadcast.device_id or group.is_locked or not group.is_active:
                    continue
                key = hashlib.sha256(f"{broadcast.id}:group:{group.id}".encode()).hexdigest()
                BroadcastRecipient.objects.get_or_create(
                    tenant=request.tenant,
                    broadcast=broadcast,
                    idempotency_key=key,
                    defaults={
                        "group": group,
                        "external_target": group.external_group_id,
                        "display_name": group.name,
                        "status": "queued",
                    },
                )
                recipient_count += 1
        broadcast.total_count = broadcast.recipients.count()
        broadcast.save(update_fields=["total_count", "updated_at"])
        log_audit(
            tenant=request.tenant,
            action="broadcast.create",
            request=request,
            obj=broadcast,
            metadata={"recipient_count": broadcast.total_count},
        )
        messages.success(request, "Broadcast disimpan sebagai draft. Tinjau semua penerima sebelum mengetik KIRIM.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    return render(
        request,
        "crm/broadcast_form.html",
        {
            "form": form,
            "personal_enabled": feature_enabled(request.tenant, "personal_broadcast", False),
            "group_enabled": feature_enabled(request.tenant, "group_broadcast", False),
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
def broadcast_detail(request, pk):
    broadcast = get_object_or_404(
        Broadcast.objects.select_related("device", "preset", "created_by"),
        pk=pk,
        tenant=request.tenant,
    )
    recipients = broadcast.recipients.select_related("contact", "group")
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    valid_statuses = {choice[0] for choice in BroadcastRecipient.STATUS_CHOICES}
    if query:
        recipients = recipients.filter(
            Q(display_name__icontains=query)
            | Q(external_target__icontains=query)
            | Q(error__icontains=query)
        )
    if status in valid_statuses:
        recipients = recipients.filter(status=status)
    status_counts = {
        row["status"]: row["total"]
        for row in broadcast.recipients.values("status").annotate(total=Count("id"))
    }
    processed = sum(
        status_counts.get(key, 0)
        for key in ("sent", "failed", "uncertain", "cancelled", "skipped")
    )
    progress_percent = int((processed / broadcast.total_count) * 100) if broadcast.total_count else 0
    feature_key = "group_broadcast" if broadcast.target_type == "group" else "personal_broadcast"
    return render(
        request,
        "crm/broadcast_detail.html",
        {
            "broadcast": broadcast,
            "recipients": recipients[:1000],
            "feature_enabled": feature_enabled(request.tenant, feature_key, False),
            "feature_key": feature_key,
            "status_counts": status_counts,
            "progress_percent": progress_percent,
            "query": query,
            "selected_status": status,
        },
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_GET
def broadcast_status_api(request, pk):
    broadcast = get_object_or_404(Broadcast, pk=pk, tenant=request.tenant)
    recipients = list(
        broadcast.recipients.order_by("created_at").values(
            "id", "status", "attempts", "sent_at", "error"
        )[:1000]
    )
    status_counts = {
        row["status"]: row["total"]
        for row in broadcast.recipients.values("status").annotate(total=Count("id"))
    }
    processed = sum(
        status_counts.get(key, 0)
        for key in ("sent", "failed", "uncertain", "cancelled", "skipped")
    )
    progress = int((processed / broadcast.total_count) * 100) if broadcast.total_count else 0
    return JsonResponse(
        {
            "status": broadcast.status,
            "status_label": broadcast.get_status_display(),
            "total_count": broadcast.total_count,
            "sent_count": broadcast.sent_count,
            "failed_count": broadcast.failed_count,
            "skipped_count": broadcast.skipped_count,
            "progress_percent": progress,
            "status_counts": status_counts,
            "recipients": recipients,
        }
    )


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def broadcast_start(request, pk):
    broadcast = get_object_or_404(Broadcast, pk=pk, tenant=request.tenant)
    feature_key = "group_broadcast" if broadcast.target_type == "group" else "personal_broadcast"
    if not feature_enabled(request.tenant, feature_key, False):
        messages.error(request, "Fitur broadcast belum diaktifkan pada System Settings → Features.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if request.POST.get("confirm") != "KIRIM":
        messages.error(request, "Ketik KIRIM persis untuk mengonfirmasi pengiriman.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if broadcast.status not in {"draft", "scheduled", "queued", "failed"}:
        messages.error(request, "Broadcast tidak dapat dimulai dari status sekarang.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if broadcast.total_count == 0:
        messages.error(request, "Broadcast tidak memiliki penerima.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    max_recipients = int(getattr(settings, "CAMPAIGN_MAX_RECIPIENTS", 500))
    if broadcast.total_count > max_recipients:
        messages.error(request, f"Batas aman rilis ini adalah {max_recipients} penerima per broadcast.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if not broadcast.device.send_enabled:
        messages.error(request, "Device pengiriman sedang dinonaktifkan.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    metadata = broadcast.metadata or {}
    if broadcast.target_type == "personal" and not metadata.get("consent_confirmed"):
        messages.error(request, "Konfirmasi consent personal tidak tercatat.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if broadcast.target_type == "group" and not metadata.get("group_permission_confirmed"):
        messages.error(request, "Konfirmasi izin pengiriman grup tidak tercatat.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    if broadcast.scheduled_at and broadcast.scheduled_at > timezone.now():
        broadcast.status = "scheduled"
        broadcast.save(update_fields=["status", "updated_at"])
        messages.success(request, "Broadcast dijadwalkan.")
    else:
        broadcast.status = "queued"
        broadcast.save(update_fields=["status", "updated_at"])
        launch_broadcast.delay(str(broadcast.id))
        messages.success(request, "Broadcast masuk antrean. Pengiriman dilakukan bertahap sesuai delay.")
    log_audit(tenant=request.tenant, action="broadcast.start", request=request, obj=broadcast)
    return redirect("broadcast_detail", pk=broadcast.pk)


@roles_required("owner", "admin", "manager", "marketing")
@require_POST
def broadcast_cancel(request, pk):
    broadcast = get_object_or_404(Broadcast, pk=pk, tenant=request.tenant)
    if broadcast.status in {"completed", "cancelled"}:
        messages.info(request, "Broadcast sudah selesai atau sudah dibatalkan.")
        return redirect("broadcast_detail", pk=broadcast.pk)
    broadcast.status = "cancelled"
    broadcast.completed_at = timezone.now()
    broadcast.save(update_fields=["status", "completed_at", "updated_at"])
    broadcast.recipients.filter(status="queued").update(status="cancelled", updated_at=timezone.now())
    log_audit(tenant=request.tenant, action="broadcast.cancel", request=request, obj=broadcast)
    messages.success(request, "Antrean yang belum dikirim telah dibatalkan.")
    return redirect("broadcast_detail", pk=broadcast.pk)


@csrf_exempt
def starsender_account_webhook(request, token):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)
    blocked = validate_webhook_request(request, token)
    if blocked:
        return blocked
    account = get_object_or_404(StarSenderAccount, webhook_token=token, is_active=True)
    if not feature_enabled(account.tenant, "starsender_multi_device", True):
        return JsonResponse({"ok": True, "queued": False, "ignored": "feature_disabled"})
    try:
        payload = json.loads(request.body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    digest = hashlib.sha256(request.body).hexdigest()
    parsed = parse_starsender(payload)
    parsed_device = parsed.get("device", "")
    device = account.devices.filter(external_device_id=parsed_device).first() if parsed_device else None
    event, created = StarSenderInboundEvent.objects.get_or_create(
        tenant=account.tenant,
        account=account,
        payload_hash=digest,
        defaults={
            "device": device,
            "external_event_id": parsed.get("message_id", ""),
            "payload": payload,
        },
    )
    if created:
        process_starsender_account_event.delay(str(event.id))
    return JsonResponse({"ok": True, "queued": created})
