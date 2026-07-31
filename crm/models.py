import secrets
import uuid
from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Tenant(UUIDModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    timezone = models.CharField(max_length=50, default="Asia/Jakarta")

    def __str__(self):
        return self.name


class Membership(UUIDModel):
    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("admin", "Admin"),
        ("manager", "Manager"),
        ("sales", "Sales"),
        ("cs", "Customer Service"),
        ("marketing", "Marketing"),
        ("viewer", "Viewer"),
    ]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="viewer")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "user"], name="unique_tenant_user")]


class TenantOwnedModel(UUIDModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class Brand(TenantOwnedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150)
    description = models.TextField(blank=True)
    primary_color = models.CharField(max_length=20, default="#2563eb")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "slug"], name="unique_brand_slug")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Agent(TenantOwnedModel):
    MODE_CHOICES = [
        ("draft", "Draft only"),
        ("approval", "Perlu persetujuan"),
        ("limited", "Auto reply terbatas"),
        ("autonomous", "Autonomous"),
        ("human", "Human only"),
    ]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="agents")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    system_prompt = models.TextField()
    greeting = models.TextField(blank=True)
    tone = models.CharField(max_length=100, default="Profesional, ramah, dan ringkas")
    language = models.CharField(max_length=50, default="Bahasa Indonesia")
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default="draft")
    confidence_threshold = models.PositiveSmallIntegerField(default=75)
    handoff_keywords = models.TextField(default="manusia,admin,cs,komplain,refund,pengacara")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["brand__name", "name"]

    def __str__(self):
        return f"{self.brand.name} — {self.name}"


class KnowledgeEntry(TenantOwnedModel):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="knowledge_entries")
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    content = models.TextField()
    source_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["agent__name", "title"]
        indexes = [models.Index(fields=["tenant", "agent", "is_active"])]

    def __str__(self):
        return self.title


class Contact(TenantOwnedModel):
    STATUS_CHOICES = [("lead", "Lead"), ("customer", "Customer"), ("inactive", "Inactive")]
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="contacts")
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_contacts")
    name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    company = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="lead")
    lead_score = models.IntegerField(default=0)
    tags = models.JSONField(default=list, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    marketing_consent = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_activity_at"]
        indexes = [
            models.Index(fields=["tenant", "phone"]),
            models.Index(fields=["tenant", "email"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self):
        return self.name or self.phone or self.email or str(self.id)


class Pipeline(TenantOwnedModel):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="pipelines")
    name = models.CharField(max_length=150)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class PipelineStage(TenantOwnedModel):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=120)
    position = models.PositiveIntegerField(default=0)
    probability = models.PositiveSmallIntegerField(default=0)
    color = models.CharField(max_length=20, default="#64748b")

    class Meta:
        ordering = ["position", "created_at"]
        constraints = [models.UniqueConstraint(fields=["pipeline", "name"], name="unique_stage_name")]

    def __str__(self):
        return f"{self.pipeline.name} — {self.name}"


class Deal(TenantOwnedModel):
    STATUS_CHOICES = [("open", "Open"), ("won", "Won"), ("lost", "Lost")]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="deals")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="deals")
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name="deals")
    stage = models.ForeignKey(PipelineStage, on_delete=models.PROTECT, related_name="deals")
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="deals")
    title = models.CharField(max_length=200)
    value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    expected_close_date = models.DateField(null=True, blank=True)
    lost_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-updated_at"]


class ChannelConnection(TenantOwnedModel):
    PROVIDER_CHOICES = [("starsender", "StarSender"), ("mailketing", "Mailketing"), ("webchat", "Web Chat")]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="connections")
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="connections")
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)
    name = models.CharField(max_length=150)
    external_id = models.CharField(max_length=200, blank=True)
    encrypted_credentials = models.TextField(blank=True)
    settings = models.JSONField(default=dict, blank=True)
    webhook_token = models.CharField(max_length=80, default=secrets.token_urlsafe, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["provider", "name"]

    def __str__(self):
        return f"{self.get_provider_display()} — {self.name}"


class Conversation(TenantOwnedModel):
    STATUS_CHOICES = [("open", "Open"), ("pending", "Pending"), ("closed", "Closed")]
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="conversations")
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="conversations")
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations")
    connection = models.ForeignKey(ChannelConnection, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversations")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_conversations")
    channel = models.CharField(max_length=30, default="whatsapp")
    external_thread_id = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    ai_enabled = models.BooleanField(default=False)
    needs_handoff = models.BooleanField(default=False)
    handoff_reason = models.CharField(max_length=250, blank=True)
    last_message_at = models.DateTimeField(default=timezone.now)
    unread_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [models.Index(fields=["tenant", "status", "last_message_at"])]

    def __str__(self):
        return f"{self.contact} ({self.channel})"


class Message(TenantOwnedModel):
    DIRECTION_CHOICES = [("inbound", "Inbound"), ("outbound", "Outbound"), ("internal", "Internal")]
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("delivered", "Delivered"),
        ("read", "Read"),
        ("failed", "Failed"),
        ("uncertain", "Status tidak pasti"),
    ]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    sender_type = models.CharField(max_length=20, default="contact")
    sender_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.TextField(blank=True)
    message_type = models.CharField(max_length=30, default="text")
    attachment_url = models.URLField(blank=True)
    provider_message_id = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    raw_payload = models.JSONField(default=dict, blank=True)
    ai_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["tenant", "provider_message_id"])]


class Task(TenantOwnedModel):
    STATUS_CHOICES = [("todo", "To do"), ("doing", "Doing"), ("done", "Done")]
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="crm_tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="todo")
    priority = models.CharField(max_length=20, default="normal")

    class Meta:
        ordering = ["status", "due_at"]


class WebhookEvent(TenantOwnedModel):
    provider = models.CharField(max_length=30)
    connection = models.ForeignKey(ChannelConnection, on_delete=models.SET_NULL, null=True, blank=True)
    external_event_id = models.CharField(max_length=200, blank=True)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default="received")
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["provider", "payload_hash"])]
        constraints = [models.UniqueConstraint(fields=["tenant", "provider", "payload_hash"], name="unique_tenant_provider_payload")]


class UsageRecord(TenantOwnedModel):
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=50)
    units = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    estimated_cost = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class AuditLog(TenantOwnedModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

class Campaign(TenantOwnedModel):
    CHANNEL_CHOICES = [("whatsapp", "WhatsApp"), ("email", "Email")]
    STATUS_CHOICES = [("draft", "Draft"), ("queued", "Queued"), ("running", "Running"), ("completed", "Completed"), ("failed", "Failed")]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="campaigns")
    connection = models.ForeignKey(ChannelConnection, on_delete=models.PROTECT, related_name="campaigns")
    name = models.CharField(max_length=180)
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES)
    subject = models.CharField(max_length=200, blank=True)
    content = models.TextField()
    tag_filter = models.CharField(max_length=100, blank=True, help_text="Opsional: hanya kontak dengan tag ini")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CampaignRecipient(TenantOwnedModel):
    STATUS_CHOICES = [("queued", "Queued"), ("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")]
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="campaign_recipients")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    provider_response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["campaign", "contact"], name="unique_campaign_contact")]
        ordering = ["created_at"]


class KnowledgeRevision(TenantOwnedModel):
    entry = models.ForeignKey(KnowledgeEntry, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    content = models.TextField()
    source_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-revision_number", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "revision_number"],
                name="unique_knowledge_revision_number",
            )
        ]


class AIReview(TenantOwnedModel):
    VERDICT_CHOICES = [
        ("helpful", "Jawaban tepat"),
        ("needs_edit", "Perlu koreksi"),
        ("unsafe", "Tidak aman"),
    ]
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    verdict = models.CharField(max_length=20, choices=VERDICT_CHOICES)
    comment = models.TextField(blank=True)
    corrected_response = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["message", "reviewer"],
                name="unique_message_reviewer",
            )
        ]


class AutomationRule(TenantOwnedModel):
    TRIGGER_CHOICES = [
        ("new_contact", "Kontak baru"),
        ("inbound_message", "Pesan masuk"),
        ("handoff", "Handoff ke manusia"),
        ("deal_stage", "Perubahan tahap deal"),
        ("no_reply", "Tidak ada balasan"),
        ("scheduled", "Terjadwal"),
    ]
    ACTION_CHOICES = [
        ("tag_contact", "Tambahkan tag"),
        ("create_task", "Buat tugas"),
        ("send_whatsapp", "Kirim WhatsApp"),
        ("call_webhook", "Panggil webhook / n8n"),
        ("enable_ai", "Aktifkan AI"),
        ("disable_ai", "Nonaktifkan AI"),
        ("move_stage", "Pindahkan tahap deal"),
    ]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, null=True, blank=True, related_name="automation_rules")
    name = models.CharField(max_length=180)
    trigger = models.CharField(max_length=30, choices=TRIGGER_CHOICES)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    config = models.JSONField(default=dict, blank=True)
    delay_minutes = models.PositiveIntegerField(default=0)
    cooldown_minutes = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["tenant", "trigger", "is_active"])]

    def __str__(self):
        return self.name


class AutomationRun(TenantOwnedModel):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name="runs")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True)
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True, blank=True)
    deal = models.ForeignKey(Deal, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    unique_key = models.CharField(max_length=250, null=True, blank=True)
    trigger_payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(default=timezone.now)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "rule", "unique_key"],
                name="unique_automation_run_key",
            )
        ]
        indexes = [models.Index(fields=["status", "scheduled_for"])]


class Subscription(TenantOwnedModel):
    PLAN_CHOICES = [
        ("starter", "Starter"),
        ("growth", "Growth"),
        ("business", "Business"),
        ("enterprise", "Enterprise"),
    ]
    STATUS_CHOICES = [
        ("trial", "Trial"),
        ("active", "Active"),
        ("past_due", "Past due"),
        ("suspended", "Suspended"),
        ("cancelled", "Cancelled"),
    ]
    plan = models.CharField(max_length=30, choices=PLAN_CHOICES, default="starter")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="active")
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class BackupRecord(TenantOwnedModel):
    STATUS_CHOICES = [
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="running")
    filename = models.CharField(max_length=250, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class FeatureFlag(TenantOwnedModel):
    """Runtime feature switches stored in the database.

    Dangerous features default to disabled and can be enabled from the dashboard
    without rebuilding or redeploying the application.
    """

    key = models.SlugField(max_length=100)
    label = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    is_dangerous = models.BooleanField(default=False)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "key"], name="unique_tenant_feature_flag")
        ]

    def __str__(self):
        return f"{self.tenant.name} — {self.label}"


class AgentRuntimePolicy(TenantOwnedModel):
    """Operational guardrails kept outside the legacy Agent table."""

    agent = models.OneToOneField(Agent, on_delete=models.CASCADE, related_name="runtime_policy")
    clarification_threshold = models.PositiveSmallIntegerField(default=45)
    reply_threshold = models.PositiveSmallIntegerField(default=70)
    auto_handoff_low_confidence = models.BooleanField(default=False)
    max_reply_chars = models.PositiveIntegerField(default=1200)
    max_questions = models.PositiveSmallIntegerField(default=2)
    reply_delay_seconds = models.PositiveSmallIntegerField(default=0)
    safe_clarification_text = models.TextField(
        default=(
            "Saya perlu sedikit informasi tambahan agar jawabannya tepat. "
            "Boleh jelaskan kebutuhan utama Anda secara singkat?"
        )
    )
    hard_handoff_keywords = models.TextField(
        default=(
            "manusia,admin,customer service,staf,konsultan,notaris,pengacara,"
            "komplain,refund,pengembalian dana,sengketa,marah,kecewa"
        )
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["agent__brand__name", "agent__name"]

    def __str__(self):
        return f"Policy — {self.agent}"


class ConversationControl(TenantOwnedModel):
    STATE_CHOICES = [
        ("ai_active", "AI Active"),
        ("clarification", "Clarification Needed"),
        ("waiting_human", "Waiting for Human"),
        ("human_active", "Human Active"),
        ("resolved", "Resolved"),
    ]
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="control"
    )
    state = models.CharField(max_length=30, choices=STATE_CHOICES, default="ai_active")
    last_confidence = models.PositiveSmallIntegerField(default=0)
    handoff_count = models.PositiveIntegerField(default=0)
    last_ai_error = models.TextField(blank=True)
    last_handoff_at = models.DateTimeField(null=True, blank=True)
    last_ai_reply_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]


class ContactMemory(TenantOwnedModel):
    contact = models.OneToOneField(Contact, on_delete=models.CASCADE, related_name="memory")
    summary = models.TextField(blank=True)
    facts = models.JSONField(default=dict, blank=True)
    missing_fields = models.JSONField(default=list, blank=True)
    last_extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]


class AppNotification(TenantOwnedModel):
    LEVEL_CHOICES = [
        ("info", "Info"),
        ("warning", "Warning"),
        ("error", "Error"),
        ("critical", "Critical"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="info")
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    dedupe_key = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "is_read", "created_at"])]


class StarSenderAccount(TenantOwnedModel):
    name = models.CharField(max_length=150)
    encrypted_account_key = models.TextField(blank=True)
    webhook_token = models.CharField(max_length=100, default=secrets.token_urlsafe, unique=True)
    is_active = models.BooleanField(default=True)
    auto_sync_devices = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=30, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_tenant_starsender_account")
        ]

    def __str__(self):
        return self.name


class StarSenderDevice(TenantOwnedModel):
    STATUS_CHOICES = [
        ("connected", "Connected"),
        ("disconnected", "Disconnected"),
        ("scanning", "Scanning"),
        ("unknown", "Unknown"),
        ("disabled", "Disabled"),
        ("credential_missing", "Credential Missing"),
    ]
    account = models.ForeignKey(
        StarSenderAccount, on_delete=models.CASCADE, related_name="devices"
    )
    external_device_id = models.CharField(max_length=120)
    name = models.CharField(max_length=180, blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    connection_status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default="unknown"
    )
    encrypted_device_key = models.TextField(blank=True)
    brand = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="starsender_devices"
    )
    agent = models.ForeignKey(
        Agent, on_delete=models.SET_NULL, null=True, blank=True, related_name="starsender_devices"
    )
    connection = models.OneToOneField(
        ChannelConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="starsender_device",
    )
    send_enabled = models.BooleanField(default=False)
    group_sync_enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    is_fallback = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_group_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["account__name", "name", "phone_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_device_id"], name="unique_starsender_account_device"
            )
        ]
        indexes = [models.Index(fields=["tenant", "connection_status", "send_enabled"])]

    def __str__(self):
        return self.name or self.phone_number or self.external_device_id


class WhatsAppGroup(TenantOwnedModel):
    AI_MODE_CHOICES = [
        ("off", "AI Nonaktif"),
        ("mention", "AI hanya saat disebut"),
        ("draft", "AI membuat draft"),
        ("autonomous", "AI Autonomous"),
    ]
    device = models.ForeignKey(
        StarSenderDevice, on_delete=models.CASCADE, related_name="whatsapp_groups"
    )
    external_group_id = models.CharField(max_length=200)
    name = models.CharField(max_length=220)
    contact = models.OneToOneField(
        Contact, on_delete=models.SET_NULL, null=True, blank=True, related_name="whatsapp_group"
    )
    is_active = models.BooleanField(default=True)
    is_locked = models.BooleanField(
        default=False,
        help_text="Grup yang dikunci tidak dapat menjadi tujuan broadcast.",
    )
    ai_mode = models.CharField(max_length=20, choices=AI_MODE_CHOICES, default="off")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["device__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["device", "external_group_id"], name="unique_device_whatsapp_group"
            )
        ]
        indexes = [models.Index(fields=["tenant", "is_active", "name"])]

    def __str__(self):
        return f"{self.name} — {self.device}"


class GroupCategory(TenantOwnedModel):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_tenant_group_category")
        ]

    def __str__(self):
        return self.name


class GroupCategoryMembership(TenantOwnedModel):
    group = models.ForeignKey(WhatsAppGroup, on_delete=models.CASCADE, related_name="category_links")
    category = models.ForeignKey(GroupCategory, on_delete=models.CASCADE, related_name="group_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "category"], name="unique_group_category_link")
        ]


class GroupPreset(TenantOwnedModel):
    TYPE_CHOICES = [("static", "Statis"), ("dynamic", "Dinamis berdasarkan kategori")]
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    preset_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="static")
    brand = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="group_presets"
    )
    category = models.ForeignKey(
        GroupCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="presets"
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_tenant_group_preset")
        ]

    def __str__(self):
        return self.name


class GroupPresetMember(TenantOwnedModel):
    preset = models.ForeignKey(GroupPreset, on_delete=models.CASCADE, related_name="members")
    group = models.ForeignKey(WhatsAppGroup, on_delete=models.CASCADE, related_name="preset_links")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["preset", "group"], name="unique_preset_group_member")
        ]


class StarSenderInboundEvent(TenantOwnedModel):
    STATUS_CHOICES = [
        ("received", "Received"),
        ("processing", "Processing"),
        ("processed", "Processed"),
        ("ignored", "Ignored"),
        ("needs_mapping", "Needs Mapping"),
        ("retrying", "Retrying"),
        ("failed", "Failed"),
    ]
    account = models.ForeignKey(
        StarSenderAccount, on_delete=models.CASCADE, related_name="inbound_events"
    )
    device = models.ForeignKey(
        StarSenderDevice, on_delete=models.SET_NULL, null=True, blank=True, related_name="inbound_events"
    )
    external_event_id = models.CharField(max_length=200, blank=True)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="received")
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "payload_hash"], name="unique_starsender_account_payload"
            )
        ]
        indexes = [models.Index(fields=["tenant", "status", "created_at"])]


class Broadcast(TenantOwnedModel):
    TARGET_CHOICES = [("personal", "Personal"), ("group", "Grup")]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("scheduled", "Scheduled"),
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("failed", "Failed"),
    ]
    name = models.CharField(max_length=180)
    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    device = models.ForeignKey(
        StarSenderDevice, on_delete=models.PROTECT, related_name="broadcasts"
    )
    preset = models.ForeignKey(
        GroupPreset, on_delete=models.SET_NULL, null=True, blank=True, related_name="broadcasts"
    )
    message_type = models.CharField(max_length=20, default="text")
    body = models.TextField(blank=True)
    file_url = models.URLField(blank=True)
    delay_seconds = models.PositiveIntegerField(default=30)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    total_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status", "scheduled_at"])]

    def __str__(self):
        return self.name


class BroadcastRecipient(TenantOwnedModel):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("uncertain", "Status tidak pasti"),
        ("cancelled", "Cancelled"),
        ("skipped", "Skipped"),
    ]
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True)
    group = models.ForeignKey(WhatsAppGroup, on_delete=models.SET_NULL, null=True, blank=True)
    external_target = models.CharField(max_length=220)
    display_name = models.CharField(max_length=220, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    provider_message_id = models.CharField(max_length=200, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=64)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "idempotency_key"], name="unique_broadcast_recipient_key"
            )
        ]
        indexes = [models.Index(fields=["status", "scheduled_for"])]
