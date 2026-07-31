from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from crm.models import (
    Broadcast,
    FeatureFlag,
    StarSenderAccount,
    StarSenderDevice,
    Tenant,
    WhatsAppGroup,
)
from crm.services.crypto import decrypt_dict
from crm.services.features import ensure_default_flags


class Command(BaseCommand):
    help = "Validate critical V4 configuration and additive tables"

    def add_arguments(self, parser):
        parser.add_argument(
            "--startup",
            action="store_true",
            help="Use concise startup output and fail only on critical issues.",
        )

    def handle(self, *args, **options):
        critical: list[str] = []
        warnings: list[str] = []

        if not settings.APP_ENCRYPTION_KEY:
            critical.append("APP_ENCRYPTION_KEY belum diatur")
        if settings.SECRET_KEY == "unsafe-dev-key":
            critical.append("DJANGO_SECRET_KEY masih memakai nilai development")
        if not settings.APP_BASE_URL.startswith("https://") and not settings.DEBUG:
            warnings.append("APP_BASE_URL belum menggunakan HTTPS")
        if settings.ALLOWED_HOSTS == ["*"] and not settings.DEBUG:
            warnings.append("ALLOWED_HOSTS masih '*' pada mode produksi")

        required_tables = {
            FeatureFlag._meta.db_table,
            StarSenderAccount._meta.db_table,
            StarSenderDevice._meta.db_table,
            WhatsAppGroup._meta.db_table,
            Broadcast._meta.db_table,
        }
        existing_tables = set(connection.introspection.table_names())
        missing = sorted(required_tables - existing_tables)
        if missing:
            critical.append("Tabel V4 belum terbentuk: " + ", ".join(missing))

        for tenant in Tenant.objects.filter(is_active=True):
            ensure_default_flags(tenant)

        for account in StarSenderAccount.objects.exclude(encrypted_account_key="")[:20]:
            try:
                if not decrypt_dict(account.encrypted_account_key).get("account_api_key"):
                    warnings.append(f"Account API Key kosong: {account.name}")
            except Exception:
                critical.append(f"Account API Key tidak dapat didekripsi: {account.name}")

        for device in StarSenderDevice.objects.exclude(encrypted_device_key="")[:100]:
            try:
                if not decrypt_dict(device.encrypted_device_key).get("device_key"):
                    warnings.append(f"Device Key kosong: {device}")
            except Exception:
                critical.append(f"Device Key tidak dapat didekripsi: {device}")

        try:
            cache.set("v4-preflight", "ok", timeout=10)
            if cache.get("v4-preflight") != "ok":
                warnings.append("Redis cache tidak merespons normal")
        except Exception as exc:
            warnings.append(f"Redis belum dapat diuji: {exc}")

        for item in warnings:
            self.stdout.write(self.style.WARNING("WARNING: " + item))
        if critical:
            for item in critical:
                self.stderr.write(self.style.ERROR("CRITICAL: " + item))
            raise CommandError("V4 preflight gagal")

        self.stdout.write(self.style.SUCCESS("V4 preflight berhasil"))
