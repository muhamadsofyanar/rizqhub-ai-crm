from .models import Brand


def app_context(request):
    tenant = getattr(request, "tenant", None)
    return {
        "current_tenant": tenant,
        "current_membership": getattr(request, "membership", None),
        "tenant_brands": Brand.objects.filter(tenant=tenant, is_active=True) if tenant else [],
    }
