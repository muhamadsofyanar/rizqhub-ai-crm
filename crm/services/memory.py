from __future__ import annotations

import re
from django.utils import timezone

from crm.models import ContactMemory


FIELD_LABELS = {
    "name": "Nama",
    "entity_type": "Jenis badan usaha/layanan",
    "business_field": "Bidang usaha",
    "domicile": "Domisili",
    "founder_count": "Jumlah pendiri",
    "target_time": "Target waktu",
    "budget": "Anggaran",
}


def _clean(value: str) -> str:
    return " ".join((value or "").strip(" .,:;-").split())[:180]


def extract_facts(text: str) -> dict:
    raw = text or ""
    lowered = raw.lower()
    facts = {}

    entity_patterns = [
        (r"\bpt perorangan\b", "PT Perorangan"),
        (r"\bpt biasa\b", "PT Biasa"),
        (r"\bperseroan terbatas\b|\bbuat pt\b|\bmembuat pt\b", "PT"),
        (r"\bcv\b", "CV"),
        (r"\byayasan\b", "Yayasan"),
        (r"\bperkumpulan\b", "Perkumpulan"),
    ]
    for pattern, value in entity_patterns:
        if re.search(pattern, lowered):
            facts["entity_type"] = value
            break

    m = re.search(r"(?:domisili|lokasi|berada|alamat usaha)\s*(?:di|:)?\s*([A-Za-zÀ-ÿ .-]{3,60})", raw, re.I)
    if m:
        facts["domicile"] = _clean(m.group(1).split(" dan ")[0])
    else:
        m = re.search(r"\bdi\s+(Jakarta|Bandung|Bekasi|Bogor|Depok|Tangerang|Surabaya|Semarang|Yogyakarta|Jogja|Madiun|Malang|Bali)\b", raw, re.I)
        if m:
            facts["domicile"] = _clean(m.group(1))

    m = re.search(r"(?:jumlah pendiri|pendiri|pemegang saham)\s*(?:ada|:)?\s*(\d{1,2})", lowered)
    if not m:
        m = re.search(r"\b(\d{1,2})\s*(?:orang|pendiri|pemegang saham)\b", lowered)
    if m:
        facts["founder_count"] = int(m.group(1))

    m = re.search(r"(?:bidang usaha|usaha|bisnis)\s*(?:saya|kami)?\s*(?:adalah|di bidang|:)?\s*([A-Za-zÀ-ÿ0-9 &/.-]{3,80})", raw, re.I)
    if m:
        candidate = _clean(m.group(1).split(" dan domisili")[0].split(" di kota")[0])
        if candidate.lower() not in {"apa", "apa saja", "ini", "tersebut"}:
            facts["business_field"] = candidate

    m = re.search(r"(?:target|selesai|butuh)\s*(?:pada|di|bulan|sebelum)?\s*([A-Za-zÀ-ÿ0-9 /.-]{3,50})", raw, re.I)
    if m:
        facts["target_time"] = _clean(m.group(1))

    m = re.search(r"(?:budget|anggaran)\s*(?:sekitar|:)?\s*(Rp\.?\s*)?([0-9.,]+\s*(?:juta|jt|ribu|rb)?)", raw, re.I)
    if m:
        facts["budget"] = _clean("".join(part or "" for part in m.groups()))

    m = re.search(r"(?:nama saya|saya bernama|nama:)\s*([A-Za-zÀ-ÿ .'-]{2,60})", raw, re.I)
    if m:
        facts["name"] = _clean(m.group(1))

    return facts


def update_contact_memory(contact, text: str):
    memory, _ = ContactMemory.objects.get_or_create(tenant=contact.tenant, contact=contact)
    incoming = extract_facts(text)
    facts = {**(memory.facts or {}), **{k: v for k, v in incoming.items() if v not in ("", None)}}
    required = ["entity_type", "business_field", "domicile", "founder_count", "target_time"]
    missing = [key for key in required if not facts.get(key)]
    summary_lines = [f"{FIELD_LABELS.get(key, key)}: {value}" for key, value in facts.items() if value not in ("", None)]
    memory.facts = facts
    memory.missing_fields = missing
    memory.summary = "\n".join(summary_lines)
    memory.last_extracted_at = timezone.now()
    memory.save()

    changes = {}
    if facts.get("name") and not contact.name:
        changes["name"] = facts["name"]
    if facts.get("domicile") and not contact.city:
        changes["city"] = facts["domicile"]
    custom = dict(contact.custom_fields or {})
    custom["ai_memory"] = facts
    custom["ai_missing_fields"] = missing
    changes["custom_fields"] = custom
    for field, value in changes.items():
        setattr(contact, field, value)
    if changes:
        contact.save(update_fields=[*changes.keys(), "updated_at"])
    return memory


def memory_context(contact) -> str:
    try:
        memory = contact.memory
    except ContactMemory.DoesNotExist:
        return "Belum ada data pelanggan yang tersimpan."
    if not memory.facts:
        return "Belum ada data pelanggan yang tersimpan."
    lines = [f"- {FIELD_LABELS.get(key, key)}: {value}" for key, value in memory.facts.items()]
    if memory.missing_fields:
        labels = [FIELD_LABELS.get(key, key) for key in memory.missing_fields]
        lines.append("- Data yang masih dibutuhkan: " + ", ".join(labels))
    return "\n".join(lines)
