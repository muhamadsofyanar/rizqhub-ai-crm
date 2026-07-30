from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView, LogoutView
from django.core.cache import cache
from django.db import connection as db_connection
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AIReviewForm,
    AgentForm,
    AgentTestForm,
    AutomationRuleForm,
    CampaignForm,
    ConnectionForm,
    ContactForm,
    ConversationAssignmentForm,
    DealStageForm,
    InternalNoteForm,
    KnowledgeEntryForm,
    MemberInviteForm,
    ReplyForm,
)
from .models import (
    AIReview,
    Agent,
    AuditLog,
    AutomationRule,
    AutomationRun,
    BackupRecord,
    Campaign,
    ChannelConnection,
    Contact,
    Conversation,
    Deal,
    KnowledgeEntry,
    KnowledgeRevision,
    Membership,
    Message,
    Pipeline,
    Subscription,
    Task,
    Tenant,
    UsageRecord,
    WebhookEvent,
)
from .services.ai import generate_reply
from .services.audit import log_audit
from .services.automations import trigger_automations
from .services.webhook_security import validate_webhook_request
from .tasks import launch_campaign, process_starsender_event, send_whatsapp_message


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
        "failed_messages": Message.objects.filter(tenant=tenant, status="failed").count(),
        "active_automations": AutomationRule.objects.filter(tenant=tenant, is_active=True).count(),
        "recent_conversations": conversations.select_related("contact", "brand", "agent")[:8],
        "recent_tasks": Task.objects.filter(tenant=tenant)
        .exclude(status="done")
        .select_related("contact", "assigned_to")[:8],
        "agents": Agent.objects.filter(tenant=tenant)
        .annotate(conversation_count=Count("conversations"))[:8],
    }
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
    form = AgentForm(request.POST or None, instance=agent, tenant=request.tenant)
    test_form = AgentTestForm()
    test_result = None
    test_meta = None
    if request.method == "POST" and request.POST.get("action") == "save" and form.is_valid():
        form.save()
        log_audit(tenant=request.tenant, action="agent.update", request=request, obj=agent)
        messages.success(request, "Agent diperbarui.")
        return redirect("agent_edit", pk=agent.pk)
    if request.method == "POST" and request.POST.get("action") == "test":
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


@tenant_required
def inbox(request):
    qs = Conversation.objects.filter(tenant=request.tenant).select_related(
        "contact", "brand", "agent", "assigned_to"
    )
    status = request.GET.get("status", "")
    if status in ["open", "pending", "closed"]:
        qs = qs.filter(status=status)
    if request.GET.get("handoff") == "1":
        qs = qs.filter(needs_handoff=True)
    return render(request, "crm/inbox.html", {"conversations": qs[:200], "selected": None})


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
            "conversations": Conversation.objects.filter(tenant=request.tenant)
            .select_related("contact", "brand", "agent")[:100],
            "selected": conversation,
            "live_poll_ms": int(settings.LIVE_INBOX_POLL_SECONDS * 1000),
        },
    )


@tenant_required
@require_GET
def conversation_messages_api(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
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


@tenant_required
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
    log_audit(
        tenant=request.tenant,
        action=f"conversation.{action}",
        request=request,
        obj=conv,
    )
    return redirect("conversation_detail", pk=conv.pk)


@tenant_required
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


@tenant_required
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


@tenant_required
@require_POST
def message_retry(request, pk):
    msg = get_object_or_404(Message, pk=pk, tenant=request.tenant, direction="outbound")
    if msg.status == "failed":
        metadata = {**(msg.ai_metadata or {}), "manual_retry_by": request.user.id}
        msg.status = "queued"
        msg.ai_metadata = metadata
        msg.save(update_fields=["status", "ai_metadata", "updated_at"])
        send_whatsapp_message.delay(str(msg.id))
        log_audit(tenant=request.tenant, action="message.retry", request=request, obj=msg)
    return redirect("conversation_detail", pk=msg.conversation_id)


@tenant_required
@require_POST
def message_approve(request, pk):
    draft = get_object_or_404(
        Message,
        pk=pk,
        tenant=request.tenant,
        direction="internal",
        sender_type="ai",
    )
    if (draft.ai_metadata or {}).get("approved_message_id"):
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
    draft.ai_metadata = {**(draft.ai_metadata or {}), "approved_message_id": str(outbound.id)}
    draft.save(update_fields=["ai_metadata", "updated_at"])
    send_whatsapp_message.delay(str(outbound.id))
    log_audit(tenant=request.tenant, action="message.approve_ai", request=request, obj=outbound)
    return redirect("conversation_detail", pk=draft.conversation_id)


@tenant_required
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
    pipelines = Pipeline.objects.filter(tenant=request.tenant).prefetch_related(
        "stages__deals__contact"
    )
    return render(request, "crm/pipeline.html", {"pipelines": pipelines})


@tenant_required
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
    return redirect("pipeline_board")


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
        "failed_messages": Message.objects.filter(tenant=request.tenant, status="failed")[:50],
        "failed_webhooks": WebhookEvent.objects.filter(tenant=request.tenant, status="failed")[:50],
        "failed_automations": AutomationRun.objects.filter(tenant=request.tenant, status="failed")[:50],
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
        "crm/form.html",
        {
            "form": form,
            "title": "Buat campaign",
            "subtitle": "Campaign hanya mengambil kontak yang memiliki consent marketing.",
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
