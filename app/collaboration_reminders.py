"""Periodic multi-level SLA notifications for configured tenants."""
from .security import Principal


def dispatch_collaboration_reminders(store, authenticator):
    if authenticator.environment != "production":
        tenants = {"local": {"admins": ["local-developer"]}}
    else:
        tenants = {}
        for entry in authenticator.keys.values():
            tenant, subject, role = str(entry.get("tenantId", "")), str(entry.get("subject", "")), entry.get("role")
            if not tenant or not subject or role not in {"reader", "requester", "legal_reviewer", "admin"}:
                continue
            group = tenants.setdefault(tenant, {"admins": []})
            if role == "admin" and subject not in group["admins"]:
                group["admins"].append(subject)
    created = []
    for tenant, group in tenants.items():
        created.extend(store.dispatch_sla_reminders(tenant_id=tenant, admin_subjects=group["admins"]))
    return created
