from django.conf import settings

from .models import Brand, Conversation


def app_context(request):
    tenant = getattr(request, "tenant", None)
    handoff_count = 0
    unread_total = 0
    if tenant:
        handoff_count = Conversation.objects.filter(
            tenant=tenant,
            needs_handoff=True,
            status__in=["open", "pending"],
        ).count()
        unread_total = sum(
            Conversation.objects.filter(tenant=tenant, unread_count__gt=0).values_list(
                "unread_count", flat=True
            )[:500]
        )
    return {
        "current_tenant": tenant,
        "current_membership": getattr(request, "membership", None),
        "tenant_brands": Brand.objects.filter(tenant=tenant, is_active=True) if tenant else [],
        "handoff_count": handoff_count,
        "unread_total": unread_total,
        "feature_live_inbox": settings.FEATURE_LIVE_INBOX,
        "feature_message_retry": settings.FEATURE_MESSAGE_RETRY,
        "feature_ai_evaluation": settings.FEATURE_AI_EVALUATION,
        "feature_automation": settings.FEATURE_AUTOMATION,
        "feature_campaign": settings.FEATURE_CAMPAIGN,
        "feature_saas": settings.FEATURE_SAAS,
        "feature_backup": settings.FEATURE_BACKUP,
    }
