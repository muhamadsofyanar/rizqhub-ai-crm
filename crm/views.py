import hashlib
import json
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .forms import AgentForm, AgentTestForm, CampaignForm, ConnectionForm, ContactForm, KnowledgeEntryForm, ReplyForm
from .models import Agent, Campaign, ChannelConnection, Contact, Conversation, Deal, KnowledgeEntry, Message, Pipeline, Task, Tenant, WebhookEvent
from .services.ai import generate_reply
from .tasks import launch_campaign, process_starsender_event, send_whatsapp_message


class AppLoginView(LoginView):
    template_name = "registration/login.html"


class AppLogoutView(LogoutView):
    pass


def health(request):
    return JsonResponse({"status": "ok", "service": "rizqhub-ai-crm"})


def tenant_required(view):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        if not request.tenant:
            return render(request, "crm/no_tenant.html", status=403)
        return view(request, *args, **kwargs)
    return wrapped


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
        "handoff_count": conversations.filter(needs_handoff=True, status__in=["open", "pending"]).count(),
        "open_deals": deals.filter(status="open").count(),
        "pipeline_value": deals.filter(status="open").aggregate(total=Sum("value"))["total"] or Decimal("0"),
        "recent_conversations": conversations.select_related("contact", "brand", "agent")[:8],
        "recent_tasks": Task.objects.filter(tenant=tenant).exclude(status="done").select_related("contact", "assigned_to")[:8],
        "agents": Agent.objects.filter(tenant=tenant).annotate(conversation_count=Count("conversations"))[:8],
    }
    return render(request, "crm/dashboard.html", context)


@tenant_required
def contact_list(request):
    qs = Contact.objects.filter(tenant=request.tenant).select_related("brand", "owner")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q) | Q(company__icontains=q))
    return render(request, "crm/contact_list.html", {"contacts": qs[:300], "q": q})


@tenant_required
def contact_create(request):
    form = ContactForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.owner = request.user
        obj.save()
        messages.success(request, "Kontak berhasil dibuat.")
        return redirect("contact_detail", pk=obj.pk)
    return render(request, "crm/form.html", {"form": form, "title": "Tambah kontak", "subtitle": "Simpan lead dan pelanggan dalam profil terpadu."})


@tenant_required
def contact_detail(request, pk):
    contact = get_object_or_404(Contact, pk=pk, tenant=request.tenant)
    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact, tenant=request.tenant)
        if form.is_valid():
            form.save()
            messages.success(request, "Kontak diperbarui.")
            return redirect("contact_detail", pk=contact.pk)
    else:
        form = ContactForm(instance=contact, tenant=request.tenant)
    return render(request, "crm/contact_detail.html", {
        "contact": contact,
        "form": form,
        "conversations": contact.conversations.select_related("agent", "brand")[:10],
        "deals": contact.deals.select_related("stage", "pipeline")[:10],
        "tasks": contact.tasks.select_related("assigned_to")[:10],
    })


@tenant_required
def agent_list(request):
    return render(request, "crm/agent_list.html", {"agents": Agent.objects.filter(tenant=request.tenant).select_related("brand")})


@tenant_required
def agent_create(request):
    form = AgentForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        messages.success(request, "Agent berhasil dibuat.")
        return redirect("agent_edit", pk=obj.pk)
    return render(request, "crm/form.html", {"form": form, "title": "Buat AI Agent", "subtitle": "Atur identitas, guardrail, dan mode operasi agent."})


@tenant_required
def agent_edit(request, pk):
    agent = get_object_or_404(Agent, pk=pk, tenant=request.tenant)
    form = AgentForm(request.POST or None, instance=agent, tenant=request.tenant)
    test_form = AgentTestForm()
    test_result = None
    if request.method == "POST" and request.POST.get("action") == "save" and form.is_valid():
        form.save()
        messages.success(request, "Agent diperbarui.")
        return redirect("agent_edit", pk=agent.pk)
    if request.method == "POST" and request.POST.get("action") == "test":
        test_form = AgentTestForm(request.POST)
        if test_form.is_valid():
            contact, _ = Contact.objects.get_or_create(tenant=request.tenant, phone="TEST-AGENT", defaults={"name": "Playground", "brand": agent.brand})
            conversation, _ = Conversation.objects.get_or_create(tenant=request.tenant, contact=contact, brand=agent.brand, agent=agent, channel="playground", defaults={"ai_enabled": False})
            try:
                test_result, _meta = generate_reply(conversation, test_form.cleaned_data["question"])
            except Exception as exc:
                test_result = f"ERROR: {exc}"
    return render(request, "crm/agent_edit.html", {"agent": agent, "form": form, "test_form": test_form, "test_result": test_result})


@tenant_required
def knowledge_list(request):
    entries = KnowledgeEntry.objects.filter(tenant=request.tenant).select_related("agent", "agent__brand")
    return render(request, "crm/knowledge_list.html", {"entries": entries})


@tenant_required
def knowledge_create(request):
    form = KnowledgeEntryForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        messages.success(request, "Knowledge entry ditambahkan.")
        return redirect("knowledge_list")
    return render(request, "crm/form.html", {"form": form, "title": "Tambah knowledge", "subtitle": "Masukkan FAQ, harga, SOP, dan informasi resmi bisnis."})


@tenant_required
def inbox(request):
    qs = Conversation.objects.filter(tenant=request.tenant).select_related("contact", "brand", "agent", "assigned_to")
    status = request.GET.get("status", "")
    if status in ["open", "pending", "closed"]:
        qs = qs.filter(status=status)
    if request.GET.get("handoff") == "1":
        qs = qs.filter(needs_handoff=True)
    return render(request, "crm/inbox.html", {"conversations": qs[:200], "selected": None})


@tenant_required
def conversation_detail(request, pk):
    conversation = get_object_or_404(Conversation.objects.select_related("contact", "brand", "agent", "connection", "assigned_to"), pk=pk, tenant=request.tenant)
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
                conversation.assigned_to = request.user
                conversation.last_message_at = timezone.now()
                conversation.save(update_fields=["ai_enabled", "needs_handoff", "assigned_to", "last_message_at", "updated_at"])
                send_whatsapp_message.delay(str(msg.id))
                messages.success(request, "Pesan masuk antrean pengiriman.")
                return redirect("conversation_detail", pk=conversation.pk)
    else:
        form = ReplyForm()
    conversation.unread_count = 0
    conversation.save(update_fields=["unread_count", "updated_at"])
    return render(request, "crm/conversation_detail.html", {
        "conversation": conversation,
        "conversation_messages": conversation.messages.select_related("sender_user"),
        "reply_form": form,
        "conversations": Conversation.objects.filter(tenant=request.tenant).select_related("contact", "brand", "agent")[:100],
        "selected": conversation,
    })


@tenant_required
@require_POST
def conversation_action(request, pk, action):
    conv = get_object_or_404(Conversation, pk=pk, tenant=request.tenant)
    if action == "enable-ai":
        conv.ai_enabled = True
        conv.needs_handoff = False
        conv.handoff_reason = ""
    elif action == "takeover":
        conv.ai_enabled = False
        conv.needs_handoff = False
        conv.assigned_to = request.user
    elif action == "close":
        conv.status = "closed"
        conv.ai_enabled = False
    elif action == "reopen":
        conv.status = "open"
    else:
        raise Http404
    conv.save()
    return redirect("conversation_detail", pk=conv.pk)


@tenant_required
def pipeline_board(request):
    pipelines = Pipeline.objects.filter(tenant=request.tenant).prefetch_related("stages__deals__contact")
    return render(request, "crm/pipeline.html", {"pipelines": pipelines})


@tenant_required
def integration_list(request):
    connections = ChannelConnection.objects.filter(tenant=request.tenant).select_related("brand", "agent")
    return render(request, "crm/integration_list.html", {"connections": connections, "app_base_url": settings.APP_BASE_URL})


@tenant_required
def integration_create(request):
    form = ConnectionForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.tenant = request.tenant
        obj.save()
        messages.success(request, "Integrasi berhasil disimpan.")
        return redirect("integration_list")
    return render(request, "crm/form.html", {"form": form, "title": "Tambah integrasi", "subtitle": "Hubungkan StarSender atau Mailketing secara aman."})


@tenant_required
def integration_edit(request, pk):
    connection = get_object_or_404(ChannelConnection, pk=pk, tenant=request.tenant)
    form = ConnectionForm(request.POST or None, instance=connection, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Integrasi diperbarui.")
        return redirect("integration_list")
    return render(request, "crm/form.html", {"form": form, "title": "Edit integrasi", "subtitle": "API credential disimpan dalam bentuk terenkripsi."})


@login_required
@require_POST
def switch_tenant(request, tenant_id):
    membership = request.user.memberships.filter(tenant_id=tenant_id, is_active=True).first()
    if membership:
        request.session["tenant_id"] = str(membership.tenant_id)
    return redirect(request.META.get("HTTP_REFERER") or "/")


@csrf_exempt
def starsender_webhook(request, token):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method_not_allowed"}, status=405)
    connection = get_object_or_404(ChannelConnection, provider="starsender", webhook_token=token, is_active=True)
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
            "tenant": connection.tenant,
            "connection": connection,
            "external_event_id": str(payload.get("message_id") or payload.get("id") or ""),
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
    connection = get_object_or_404(ChannelConnection, provider="mailketing", webhook_token=token, is_active=True)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)
    digest = hashlib.sha256(request.body).hexdigest()
    WebhookEvent.objects.get_or_create(
        tenant=connection.tenant,
        provider="mailketing",
        payload_hash=digest,
        defaults={"tenant": connection.tenant, "connection": connection, "payload": payload, "status": "received"},
    )
    email = payload.get("email")
    if email:
        contact = Contact.objects.filter(tenant=connection.tenant, email__iexact=email).first()
        if contact:
            contact.custom_fields = {**contact.custom_fields, "mailketing_last_event": payload.get("type"), "mailketing_last_event_at": str(timezone.now())}
            contact.save(update_fields=["custom_fields", "updated_at"])
    return JsonResponse({"ok": True})


@tenant_required
def campaign_list(request):
    campaigns = Campaign.objects.filter(tenant=request.tenant).select_related("brand", "connection")
    return render(request, "crm/campaign_list.html", {"campaigns": campaigns})


@tenant_required
def campaign_create(request):
    form = CampaignForm(request.POST or None, tenant=request.tenant)
    if request.method == "POST" and form.is_valid():
        campaign = form.save(commit=False)
        campaign.tenant = request.tenant
        campaign.save()
        messages.success(request, "Campaign disimpan sebagai draft. Tinjau sebelum menjalankan.")
        return redirect("campaign_detail", pk=campaign.pk)
    return render(request, "crm/form.html", {
        "form": form,
        "title": "Buat campaign",
        "subtitle": "Campaign hanya mengambil kontak yang memiliki consent marketing. Maksimal 500 penerima per peluncuran pada starter ini.",
    })


@tenant_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign.objects.select_related("brand", "connection"), pk=pk, tenant=request.tenant)
    recipients = campaign.recipients.select_related("contact")[:500]
    eligible = Contact.objects.filter(tenant=request.tenant, brand=campaign.brand, marketing_consent=True)
    if campaign.channel == "whatsapp":
        eligible = eligible.exclude(phone="")
    else:
        eligible = eligible.exclude(email="")
    if campaign.tag_filter:
        eligible = eligible.filter(tags__contains=[campaign.tag_filter])
    return render(request, "crm/campaign_detail.html", {"campaign": campaign, "recipients": recipients, "eligible_count": min(eligible.count(), 500)})


@tenant_required
@require_POST
def campaign_start(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, tenant=request.tenant)
    if campaign.status not in ["draft", "failed"]:
        messages.error(request, "Campaign ini sudah dijalankan atau sedang diproses.")
        return redirect("campaign_detail", pk=campaign.pk)
    campaign.status = "queued"
    campaign.save(update_fields=["status", "updated_at"])
    launch_campaign.delay(str(campaign.id))
    messages.success(request, "Campaign masuk antrean. Pengiriman diberi jeda untuk mengurangi risiko overload.")
    return redirect("campaign_detail", pk=campaign.pk)
