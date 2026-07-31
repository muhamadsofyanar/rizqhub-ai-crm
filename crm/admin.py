from django.contrib import admin

from .models import (
    AIReview,
    Agent,
    AgentRuntimePolicy,
    AppNotification,
    AuditLog,
    AutomationRule,
    AutomationRun,
    BackupRecord,
    Brand,
    Broadcast,
    BroadcastRecipient,
    Campaign,
    CampaignRecipient,
    ChannelConnection,
    Contact,
    ContactMemory,
    Conversation,
    ConversationControl,
    Deal,
    FeatureFlag,
    GroupCategory,
    GroupCategoryMembership,
    GroupPreset,
    GroupPresetMember,
    KnowledgeEntry,
    KnowledgeRevision,
    Membership,
    Message,
    Pipeline,
    PipelineStage,
    StarSenderAccount,
    StarSenderDevice,
    StarSenderInboundEvent,
    Subscription,
    Task,
    Tenant,
    UsageRecord,
    WebhookEvent,
    WhatsAppGroup,
)


for model in [
    Tenant,
    Membership,
    Brand,
    FeatureFlag,
    Agent,
    AgentRuntimePolicy,
    KnowledgeEntry,
    KnowledgeRevision,
    Contact,
    ContactMemory,
    Pipeline,
    PipelineStage,
    Deal,
    WhatsAppGroup,
    GroupCategory,
    GroupCategoryMembership,
    GroupPreset,
    GroupPresetMember,
    StarSenderInboundEvent,
    Conversation,
    ConversationControl,
    Message,
    Task,
    WebhookEvent,
    UsageRecord,
    AppNotification,
    AuditLog,
    Campaign,
    CampaignRecipient,
    Broadcast,
    BroadcastRecipient,
    AIReview,
    AutomationRule,
    AutomationRun,
    Subscription,
    BackupRecord,
]:
    try:
        admin.site.register(model)
    except admin.sites.AlreadyRegistered:
        pass

@admin.register(StarSenderAccount)
class StarSenderAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "is_active", "last_sync_status", "last_sync_at")
    list_filter = ("is_active", "last_sync_status", "tenant")
    search_fields = ("name",)
    exclude = ("encrypted_account_key",)
    readonly_fields = ("webhook_token", "last_sync_at", "last_sync_status", "last_error")


@admin.register(StarSenderDevice)
class StarSenderDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone_number",
        "account",
        "brand",
        "agent",
        "connection_status",
        "send_enabled",
    )
    list_filter = ("connection_status", "send_enabled", "group_sync_enabled", "tenant")
    search_fields = ("name", "phone_number", "external_device_id")
    exclude = ("encrypted_device_key",)
    readonly_fields = ("external_device_id", "last_seen_at", "last_group_sync_at", "last_error")


@admin.register(ChannelConnection)
class ChannelConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "brand", "agent", "provider", "is_active")
    list_filter = ("provider", "is_active", "tenant")
    search_fields = ("name", "external_id")
    exclude = ("encrypted_credentials",)
    readonly_fields = ("webhook_token",)

