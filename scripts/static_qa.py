#!/usr/bin/env python3
"""Static release checks that do not require Django or external services."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
WARNINGS: list[str] = []

REQUIRED_FILES = [
    "Dockerfile",
    "docker-compose.yml",
    "docker-entrypoint.sh",
    "manage.py",
    "crm/models.py",
    "crm/tasks.py",
    "crm/views.py",
    "crm/urls.py",
    "crm/services/starsender.py",
    "crm/services/inbound.py",
    "crm/services/handoff.py",
    "crm/management/commands/v4_preflight.py",
    "crm/templates/crm/inbox.html",
    "crm/templates/crm/conversation_detail.html",
    "crm/templates/crm/starsender_center.html",
    "crm/templates/crm/broadcast_form.html",
]

for relative in REQUIRED_FILES:
    if not (ROOT / relative).is_file():
        ERRORS.append(f"File wajib tidak ditemukan: {relative}")

for path in ROOT.rglob("*.py"):
    if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        ERRORS.append(f"Python tidak valid: {path.relative_to(ROOT)}: {exc}")

urls = (ROOT / "crm/urls.py").read_text(encoding="utf-8")
views = (ROOT / "crm/views.py").read_text(encoding="utf-8")
required_routes = [
    "inbox_conversations_api",
    "starsender_account_webhook",
    "starsender_account_sync",
    "starsender_device_groups_sync",
    "group_preset_create",
    "group_category_edit",
    "broadcast_start",
    "feature_toggle",
]
for name in required_routes:
    if f'name="{name}"' not in urls:
        ERRORS.append(f"URL wajib tidak ditemukan: {name}")
    if not re.search(rf"def\s+{re.escape(name)}\s*\(", views):
        ERRORS.append(f"View wajib tidak ditemukan: {name}")

models = (ROOT / "crm/models.py").read_text(encoding="utf-8")
for model in [
    "FeatureFlag",
    "AgentRuntimePolicy",
    "ConversationControl",
    "ContactMemory",
    "StarSenderAccount",
    "StarSenderDevice",
    "WhatsAppGroup",
    "GroupPreset",
    "Broadcast",
    "BroadcastRecipient",
]:
    if f"class {model}(" not in models:
        ERRORS.append(f"Model V4 tidak ditemukan: {model}")

secret_patterns = [
    re.compile(r"^GEMINI_API_KEY\s*=\s*AIza[0-9A-Za-z_-]{20,}", re.MULTILINE),
    re.compile(r"^OPENAI_API_KEY\s*=\s*sk-[0-9A-Za-z_-]{20,}", re.MULTILINE),
    re.compile(r"^POSTGRES_PASSWORD\s*=\s*(?!change-me|\$\{|<|\.\.\.|$)[^\s#]+", re.MULTILINE),
]
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix in {".pyc", ".zip", ".dump"}:
        continue
    if any(part in {".git", "__pycache__"} for part in path.parts):
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for pattern in secret_patterns:
        if pattern.search(text):
            ERRORS.append(f"Kemungkinan secret ter-commit: {path.relative_to(ROOT)}")

entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
for command in ["migrate --noinput --run-syncdb", "v4_preflight --startup", "gunicorn"]:
    if command not in entrypoint:
        ERRORS.append(f"Startup guard tidak ditemukan: {command}")

compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
for service in ["web:", "worker:", "beat:", "postgres:", "redis:"]:
    if service not in compose:
        ERRORS.append(f"Service Docker Compose tidak ditemukan: {service}")
if "condition: service_healthy" not in compose:
    WARNINGS.append("Dependency health gate tidak ditemukan pada Docker Compose")


# Safety invariants for provider delivery and credentials.
tasks_text = (ROOT / "crm/tasks.py").read_text(encoding="utf-8")
settings_text = (ROOT / "config/settings.py").read_text(encoding="utf-8")
admin_text = (ROOT / "crm/admin.py").read_text(encoding="utf-8")
backup_script = (ROOT / "scripts/backup_from_host.sh").read_text(encoding="utf-8")
for required in [
    'status = "sending"',
    'terminal_status = "uncertain" if uncertain else "failed"',
    'def recover_stale_provider_sends',
    'if parsed.get("duplicate")',
]:
    if required not in tasks_text:
        ERRORS.append(f"Provider safety invariant tidak ditemukan: {required}")
if '"recover-stale-provider-sends"' not in settings_text:
    ERRORS.append("Jadwal recovery provider send tidak ditemukan")
if '("uncertain", "Status tidak pasti")' not in models:
    ERRORS.append("Status provider tidak pasti tidak ditemukan pada model")
for sensitive_field in ["encrypted_account_key", "encrypted_device_key", "encrypted_credentials"]:
    if f'exclude = ("{sensitive_field}",' not in admin_text:
        ERRORS.append(f"Credential terenkripsi belum disembunyikan dari Django admin: {sensitive_field}")
if "pg_restore --list" not in backup_script:
    ERRORS.append("Validasi archive backup pg_restore --list tidak ditemukan")


# Role and secret-visibility invariants.
for signature in [
    '@roles_required("owner", "admin", "manager", "sales", "cs")\n@require_POST\ndef conversation_action',
    '@roles_required("owner", "admin", "manager", "sales", "cs")\n@require_POST\ndef message_retry',
    '@roles_required("owner", "admin", "manager", "marketing")\ndef starsender_center',
    '@roles_required("owner", "admin", "manager", "marketing")\ndef broadcast_detail',
]:
    if signature not in views:
        ERRORS.append(f"Role guard tidak ditemukan: {signature.splitlines()[-1]}")
integration_template = (ROOT / "crm/templates/crm/integration_list.html").read_text(encoding="utf-8")
if "Webhook URL rahasia" not in integration_template or "current_membership.role == 'owner'" not in integration_template:
    ERRORS.append("Webhook integration belum dibatasi untuk Owner/Admin")

for path in ROOT.rglob("*.html"):
    text = path.read_text(encoding="utf-8")
    if text.count("{% if") != text.count("{% endif %}"):
        ERRORS.append(f"Jumlah if/endif template tidak seimbang: {path.relative_to(ROOT)}")
    if text.count("{% for") != text.count("{% endfor %}"):
        ERRORS.append(f"Jumlah for/endfor template tidak seimbang: {path.relative_to(ROOT)}")
    if text.count("{% block") != text.count("{% endblock %}"):
        ERRORS.append(f"Jumlah block/endblock template tidak seimbang: {path.relative_to(ROOT)}")

for warning in WARNINGS:
    print(f"WARNING: {warning}")
if ERRORS:
    for error in ERRORS:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)
print(f"Static QA berhasil: {sum(1 for _ in ROOT.rglob('*.py'))} file Python diperiksa.")
