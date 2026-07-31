from __future__ import annotations

import re
from django.utils import timezone

from crm.models import AgentRuntimePolicy, ConversationControl


SMALL_TALK_PATTERNS = {
    "halo",
    "hai",
    "hi",
    "pagi",
    "siang",
    "sore",
    "malam",
    "tes",
    "test",
    "tes inbox live",
    "oke",
    "ok",
    "baik",
    "sip",
    "makasih",
    "terima kasih",
}


DEFAULT_HARD_PHRASES = {
    "bicara dengan manusia",
    "hubungkan ke manusia",
    "mau admin",
    "panggil admin",
    "customer service",
    "mau cs",
    "komplain",
    "keluhan",
    "refund",
    "pengembalian dana",
    "sengketa",
    "pengacara",
    "notaris",
    "saya marah",
    "saya kecewa",
}


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", (text or "").lower(), flags=re.UNICODE))


def is_small_talk(text: str) -> bool:
    normalized = normalize_text(text)
    if normalized in SMALL_TALK_PATTERNS:
        return True
    tokens = normalized.split()
    return bool(tokens and len(tokens) <= 4 and all(t in SMALL_TALK_PATTERNS for t in tokens))


def get_policy(agent):
    policy, _ = AgentRuntimePolicy.objects.get_or_create(
        tenant=agent.tenant,
        agent=agent,
    )
    return policy


def _phrase_match(normalized: str, phrase: str) -> bool:
    phrase = normalize_text(phrase)
    if not phrase:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized))


def explicit_handoff_reason(agent, text: str) -> str:
    normalized = normalize_text(text)
    if not normalized or is_small_talk(text):
        return ""
    policy = get_policy(agent)
    phrases = set(DEFAULT_HARD_PHRASES)
    phrases.update(
        item.strip().lower()
        for item in (policy.hard_handoff_keywords or "").split(",")
        if item.strip()
    )
    phrases.update(
        item.strip().lower()
        for item in (agent.handoff_keywords or "").split(",")
        if item.strip()
    )
    # Single ambiguous words are intentionally excluded unless they clearly ask for a human.
    ambiguous = {"admin", "cs", "staf", "manusia"}
    for phrase in sorted(phrases, key=len, reverse=True):
        if phrase in ambiguous:
            continue
        if _phrase_match(normalized, phrase):
            return f"Permintaan eskalasi terdeteksi: {phrase}"
    if re.search(r"\b(mau|ingin|tolong|hubungkan|panggil)\b.{0,30}\b(admin|cs|staf|manusia)\b", normalized):
        return "Pelanggan meminta staf manusia"
    return ""


def get_control(conversation):
    control, _ = ConversationControl.objects.get_or_create(
        tenant=conversation.tenant,
        conversation=conversation,
        defaults={"state": "ai_active" if conversation.ai_enabled else "human_active"},
    )
    return control


def set_state(conversation, state: str, *, confidence: int | None = None, error: str = ""):
    control = get_control(conversation)
    previous_state = control.state
    control.state = state
    if confidence is not None:
        control.last_confidence = max(0, min(100, int(confidence)))
    if error:
        control.last_ai_error = error[:4000]
    if state == "waiting_human" and previous_state != "waiting_human":
        control.handoff_count += 1
        control.last_handoff_at = timezone.now()
    if state in {"ai_active", "clarification"}:
        control.last_ai_reply_at = timezone.now()
        if not error:
            control.last_ai_error = ""
    control.save()
    return control
