from django.shortcuts import redirect


class CurrentTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        if request.user.is_authenticated:
            memberships = request.user.memberships.select_related("tenant").filter(is_active=True, tenant__is_active=True)
            requested_id = request.session.get("tenant_id")
            membership = memberships.filter(tenant_id=requested_id).first() if requested_id else None
            membership = membership or memberships.first()
            if membership:
                request.tenant = membership.tenant
                request.membership = membership
                request.session["tenant_id"] = str(membership.tenant_id)
        return self.get_response(request)
