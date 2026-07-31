from django.db import OperationalError, ProgrammingError

from .models import AppNotification, Brand, Conversation
from .services.features import feature_enabled


def app_context(request):
    tenant = getattr(request, "tenant", None)
    handoff_count = 0
    unread_total = 0
    notification_count = 0
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
        try:
            notification_count = AppNotification.objects.filter(
                tenant=tenant, is_read=False
            ).count()
        except (OperationalError, ProgrammingError):
            notification_count = 0
    return {
        "current_tenant": tenant,
        "current_membership": getattr(request, "membership", None),
        "tenant_brands": Brand.objects.filter(tenant=tenant, is_active=True) if tenant else [],
        "handoff_count": handoff_count,
        "unread_total": unread_total,
        "notification_count": notification_count,
        "feature_live_inbox": feature_enabled(tenant, "live_inbox", True),
        "feature_message_retry": feature_enabled(tenant, "message_retry", True),
        "feature_ai_evaluation": feature_enabled(tenant, "ai_evaluation", True),
        "feature_automation": feature_enabled(tenant, "automation", False),
        "feature_campaign": feature_enabled(tenant, "campaign", False),
        "feature_personal_broadcast": feature_enabled(tenant, "personal_broadcast", False),
        "feature_group_broadcast": feature_enabled(tenant, "group_broadcast", False),
        "feature_starsender_multi_device": feature_enabled(
            tenant, "starsender_multi_device", True
        ),
        "feature_saas": feature_enabled(tenant, "saas", False),
        "feature_backup": feature_enabled(tenant, "backup", True),
    }
