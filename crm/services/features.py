from __future__ import annotations

from django.conf import settings
from django.db import OperationalError, ProgrammingError

from crm.models import FeatureFlag


DEFAULT_FEATURES = {
    "live_inbox": {
        "label": "Inbox Live",
        "description": "Memperbarui pesan dan daftar percakapan tanpa refresh manual.",
        "enabled": True,
        "is_dangerous": False,
    },
    "message_retry": {
        "label": "Retry Pesan",
        "description": "Mencoba ulang hanya kegagalan yang diketahui aman; hasil ambigu dikarantina.",
        "enabled": True,
        "is_dangerous": False,
    },
    "ai_evaluation": {
        "label": "Evaluasi AI",
        "description": "Menyimpan confidence, sumber, dan review jawaban AI.",
        "enabled": True,
        "is_dangerous": False,
    },
    "smart_handoff": {
        "label": "Smart Handoff V2",
        "description": "Handoff hanya untuk pemicu kuat; confidence rendah meminta klarifikasi.",
        "enabled": True,
        "is_dangerous": False,
    },
    "customer_memory": {
        "label": "Customer Memory",
        "description": "Menyimpan fakta pelanggan agar AI tidak mengulang pertanyaan.",
        "enabled": True,
        "is_dangerous": False,
    },
    "starsender_multi_device": {
        "label": "StarSender Multi-Device",
        "description": "Sinkronisasi account, device, mapping brand/agent, dan grup.",
        "enabled": True,
        "is_dangerous": False,
    },
    "automation": {
        "label": "Automation & n8n",
        "description": "Menjalankan rule otomatis yang dapat mengirim pesan atau memanggil webhook.",
        "enabled": False,
        "is_dangerous": True,
    },
    "personal_broadcast": {
        "label": "Broadcast Personal",
        "description": "Mengirim pesan massal ke kontak/nomor personal yang dipilih.",
        "enabled": False,
        "is_dangerous": True,
    },
    "group_broadcast": {
        "label": "Broadcast Grup",
        "description": "Mengirim pesan ke satu atau banyak grup/preset.",
        "enabled": False,
        "is_dangerous": True,
    },
    "campaign": {
        "label": "Campaign Lama",
        "description": "Modul campaign legacy berbasis consent.",
        "enabled": False,
        "is_dangerous": True,
    },
    "saas": {
        "label": "Workspace & SaaS",
        "description": "Fitur workspace, user, subscription, dan fondasi billing.",
        "enabled": False,
        "is_dangerous": True,
    },
    "backup": {
        "label": "Backup Otomatis",
        "description": "Membuat backup PostgreSQL terjadwal.",
        "enabled": True,
        "is_dangerous": False,
    },
}


ENV_FALLBACKS = {
    "live_inbox": lambda: settings.FEATURE_LIVE_INBOX,
    "message_retry": lambda: settings.FEATURE_MESSAGE_RETRY,
    "ai_evaluation": lambda: settings.FEATURE_AI_EVALUATION,
    "automation": lambda: settings.FEATURE_AUTOMATION,
    "campaign": lambda: settings.FEATURE_CAMPAIGN,
    "saas": lambda: settings.FEATURE_SAAS,
    "backup": lambda: settings.FEATURE_BACKUP,
}


def feature_enabled(tenant, key: str, default: bool | None = None) -> bool:
    if not tenant:
        return bool(default)
    if default is None:
        default = DEFAULT_FEATURES.get(key, {}).get("enabled", False)
    try:
        row = FeatureFlag.objects.filter(tenant=tenant, key=key).only("enabled").first()
        if row:
            return bool(row.enabled)
    except (OperationalError, ProgrammingError):
        pass
    fallback = ENV_FALLBACKS.get(key)
    if fallback:
        try:
            return bool(fallback())
        except Exception:
            pass
    return bool(default)


def ensure_default_flags(tenant):
    rows = []
    for key, config in DEFAULT_FEATURES.items():
        row, _ = FeatureFlag.objects.get_or_create(
            tenant=tenant,
            key=key,
            defaults={
                "label": config["label"],
                "description": config["description"],
                "enabled": config["enabled"],
                "is_dangerous": config["is_dangerous"],
            },
        )
        rows.append(row)
    return rows
