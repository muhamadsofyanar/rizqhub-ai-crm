import re
from typing import Any

import httpx
from django.conf import settings

from crm.models import KnowledgeEntry, UsageRecord


def _tokens(text: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-zA-Z0-9À-ÿ]+", (text or "").lower())
        if len(item) > 2
    }


def retrieve_knowledge(agent, query: str, limit: int = 6):
    query_tokens = _tokens(query)
    rows = list(
        KnowledgeEntry.objects.filter(
            tenant=agent.tenant,
            agent=agent,
            is_active=True,
        )
    )
    scored = []
    for row in rows:
        haystack = _tokens(f"{row.title} {row.category} {row.content}")
        score = len(query_tokens & haystack)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [row for _, row in scored[:limit]]
    if not chosen:
        chosen = rows[: min(3, limit)]
    return chosen


def _extract_openai_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()

    texts: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                texts.append(str(text))
    return "\n".join(texts).strip()


def _extract_gemini_text(data: dict[str, Any]) -> str:
    """Return only the user-facing answer, never Gemini thought summaries."""
    texts: list[str] = []
    for candidate in data.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            # Gemini 3.x may return reasoning summaries as text parts marked
            # with thought=true. Those are internal and must not be sent to users.
            if part.get("thought") is True:
                continue
            text = part.get("text")
            if text:
                texts.append(str(text))
    return "\n".join(texts).strip()


def _build_context(conversation, inbound_text: str):
    agent = conversation.agent
    knowledge = retrieve_knowledge(agent, inbound_text)
    knowledge_text = "\n\n".join(
        f"SUMBER: {item.title}\n{item.content}" for item in knowledge
    ) or "Tidak ada sumber pengetahuan yang relevan."

    history = list(conversation.messages.order_by("-created_at")[:12])
    history.reverse()
    history_text = "\n".join(
        f"{'PELANGGAN' if message.direction == 'inbound' else 'ASISTEN'}: {message.body}"
        for message in history
        if message.body
    )

    instructions = f"""Anda adalah {agent.name}, AI customer service untuk {agent.brand.name}.
Bahasa: {agent.language}. Nada: {agent.tone}.

ATURAN UTAMA:
1. Jawab hanya berdasarkan SUMBER BISNIS yang diberikan.
2. Jangan mengarang harga, syarat, jadwal, ketentuan hukum, hasil, atau janji.
3. Bila sumber tidak cukup, katakan bahwa informasi perlu dikonfirmasi oleh tim dan tawarkan pengalihan ke CS.
4. Untuk isu hukum, medis/psikologis, refund, komplain, negosiasi, atau data sensitif, jangan memberi keputusan final.
5. Jawaban maksimal 5 paragraf pendek dan natural untuk WhatsApp.
6. Jangan menyebut istilah internal seperti RAG, prompt, confidence, atau knowledge base.

INSTRUKSI KHUSUS AGENT:
{agent.system_prompt}

SUMBER BISNIS:
{knowledge_text}
"""

    user_input = f"RIWAYAT:\n{history_text}\n\nPESAN TERBARU:\n{inbound_text}"
    return agent, knowledge, instructions, user_input


def _provider_name() -> str:
    configured = (settings.AI_PROVIDER or "auto").strip().lower()
    if configured not in {"auto", "openai", "gemini"}:
        raise RuntimeError(
            "AI_PROVIDER tidak valid. Gunakan auto, openai, atau gemini."
        )
    if configured == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY belum diatur")
        return "gemini"
    if configured == "openai":
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY belum diatur")
        return "openai"

    if settings.GEMINI_API_KEY:
        return "gemini"
    if settings.OPENAI_API_KEY:
        return "openai"
    raise RuntimeError(
        "API key AI belum diatur. Isi GEMINI_API_KEY atau OPENAI_API_KEY."
    )


def _generate_with_openai(instructions: str, user_input: str):
    payload = {
        "model": settings.OPENAI_MODEL,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": 500,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.is_error:
        raise RuntimeError(
            f"OpenAI HTTP {response.status_code}: {response.text[:800]}"
        )

    data = response.json()
    text = _extract_openai_text(data)
    if not text:
        raise RuntimeError("OpenAI tidak mengembalikan teks")

    usage = data.get("usage") or {}
    normalized_usage = {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "total_tokens": (usage.get("input_tokens", 0) or 0)
        + (usage.get("output_tokens", 0) or 0),
        "raw": usage,
    }
    return text, normalized_usage, data.get("id"), settings.OPENAI_MODEL


def _generate_with_gemini(instructions: str, user_input: str):
    model = settings.GEMINI_MODEL
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )

    # Gemini 3.x uses thinking by default. For short customer-service replies,
    # minimal thinking prevents the reasoning budget from consuming the answer
    # budget. Do not send deprecated sampling parameters such as temperature.
    token_limits = (1200, 2400)
    last_data: dict[str, Any] = {}

    with httpx.Client(timeout=90) as client:
        for max_tokens in token_limits:
            payload = {
                "system_instruction": {"parts": [{"text": instructions}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": user_input}],
                    }
                ],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "thinkingConfig": {
                        "thinkingLevel": "minimal",
                        "includeThoughts": False,
                    },
                },
            }
            response = client.post(
                endpoint,
                headers={
                    "x-goog-api-key": settings.GEMINI_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.is_error:
                raise RuntimeError(
                    f"Gemini HTTP {response.status_code}: {response.text[:800]}"
                )

            data = response.json()
            last_data = data
            finish_reasons = [
                candidate.get("finishReason")
                for candidate in data.get("candidates", [])
                if candidate.get("finishReason")
            ]

            # Never send an incomplete response. Retry once with a larger budget.
            if "MAX_TOKENS" in finish_reasons and max_tokens != token_limits[-1]:
                continue

            text = _extract_gemini_text(data)
            if "MAX_TOKENS" in finish_reasons:
                raise RuntimeError(
                    "Jawaban Gemini terpotong karena batas token. "
                    "Periksa panjang system prompt dan Knowledge Base."
                )
            if text:
                usage = data.get("usageMetadata") or {}
                normalized_usage = {
                    "input_tokens": usage.get("promptTokenCount", 0) or 0,
                    "output_tokens": usage.get("candidatesTokenCount", 0) or 0,
                    "total_tokens": usage.get("totalTokenCount", 0) or 0,
                    "raw": usage,
                }
                return text, normalized_usage, data.get("responseId"), model

    prompt_feedback = last_data.get("promptFeedback") or {}
    block_reason = prompt_feedback.get("blockReason")
    finish_reasons = [
        candidate.get("finishReason")
        for candidate in last_data.get("candidates", [])
        if candidate.get("finishReason")
    ]
    detail = block_reason or ", ".join(finish_reasons) or "respons kosong"
    raise RuntimeError(f"Gemini tidak mengembalikan teks: {detail}")


def generate_reply(conversation, inbound_text: str) -> tuple[str, dict]:
    agent, knowledge, instructions, user_input = _build_context(
        conversation,
        inbound_text,
    )
    provider = _provider_name()

    if provider == "gemini":
        text, usage, response_id, model = _generate_with_gemini(
            instructions,
            user_input,
        )
    else:
        text, usage, response_id, model = _generate_with_openai(
            instructions,
            user_input,
        )

    UsageRecord.objects.create(
        tenant=conversation.tenant,
        agent=agent,
        kind="ai_response",
        units=usage.get("total_tokens", 0) or 0,
        metadata={
            "provider": provider,
            "model": model,
            "usage": usage,
            "response_id": response_id,
        },
    )
    return text, {
        "provider": provider,
        "model": model,
        "sources": [str(item.id) for item in knowledge],
        "usage": usage,
        "response_id": response_id,
    }
