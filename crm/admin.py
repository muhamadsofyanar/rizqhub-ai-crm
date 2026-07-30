from django.contrib import admin

from .models import (
    AIReview,
    Agent,
    AuditLog,
    AutomationRule,
    AutomationRun,
    BackupRecord,
    Brand,
    Campaign,
    CampaignRecipient,
    ChannelConnection,
    Contact,
    Conversation,
    Deal,
    KnowledgeEntry,
    KnowledgeRevision,
    Membership,
    Message,
    Pipeline,
    PipelineStage,
    Subscription,
    Task,
    Tenant,
    UsageRecord,
    WebhookEvent,
)

for model in [
    Tenant,
    Membership,
    Brand,
    Agent,
    KnowledgeEntry,
    KnowledgeRevision,
    Contact,
    Pipeline,
    PipelineStage,
    Deal,
    ChannelConnection,
    Conversation,
    Message,
    Task,
    WebhookEvent,
    UsageRecord,
    AuditLog,
    Campaign,
    CampaignRecipient,
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
