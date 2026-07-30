import re
import httpx
from django.conf import settings
from crm.models import KnowledgeEntry, Message, UsageRecord


def _tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-Z0-9À-ÿ]+", (text or "").lower()) if len(x) > 2}


def retrieve_knowledge(agent, query: str, limit: int = 6):
    query_tokens = _tokens(query)
    rows = list(KnowledgeEntry.objects.filter(tenant=agent.tenant, agent=agent, is_active=True))
    scored = []
    for row in rows:
        hay = _tokens(f"{row.title} {row.category} {row.content}")
        score = len(query_tokens & hay)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [row for _, row in scored[:limit]]
    if not chosen:
        chosen = rows[: min(3, limit)]
    return chosen


def _extract_output_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    texts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()


def generate_reply(conversation, inbound_text: str) -> tuple[str, dict]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY belum diatur")
    agent = conversation.agent
    knowledge = retrieve_knowledge(agent, inbound_text)
    knowledge_text = "\n\n".join(
        f"SUMBER: {item.title}\n{item.content}" for item in knowledge
    ) or "Tidak ada sumber pengetahuan yang relevan."
    history = list(conversation.messages.order_by("-created_at")[:12])
    history.reverse()
    history_text = "\n".join(
        f"{'PELANGGAN' if m.direction == 'inbound' else 'ASISTEN'}: {m.body}" for m in history if m.body
    )
    instructions = f"""Anda adalah {agent.name}, AI customer service untuk {agent.brand.name}.
Bahasa: {agent.language}. Nada: {agent.tone}.

ATURAN UTAMA:
1. Jawab hanya berdasarkan SUMBER BISNIS yang diberikan.
2. Jangan mengarang harga, syarat, jadwal, ketentuan hukum, hasil, atau janji.
3. Bila sumber tidak cukup, katakan bahwa informasi perlu dikonfirmasi oleh tim dan tawarkan pengalihan ke CS.
4. Untuk isu hukum, medis/psikologis, refund, komplain, negosiasi, atau data sensitif, jangan memberi keputusan final.
5. Jawaban maksimal 5 paragraf pendek, natural untuk WhatsApp.
6. Jangan menyebut istilah internal seperti RAG, prompt, confidence, atau knowledge base.

INSTRUKSI KHUSUS AGENT:
{agent.system_prompt}

SUMBER BISNIS:
{knowledge_text}
"""
    payload = {
        "model": settings.OPENAI_MODEL,
        "instructions": instructions,
        "input": f"RIWAYAT:\n{history_text}\n\nPESAN TERBARU:\n{inbound_text}",
        "max_output_tokens": 500,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.is_error:
        raise RuntimeError(f"OpenAI HTTP {response.status_code}: {response.text[:800]}")
    data = response.json()
    text = _extract_output_text(data)
    if not text:
        raise RuntimeError("OpenAI tidak mengembalikan teks")
    usage = data.get("usage") or {}
    UsageRecord.objects.create(
        tenant=conversation.tenant,
        agent=agent,
        kind="ai_response",
        units=(usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0),
        metadata={"model": settings.OPENAI_MODEL, "usage": usage, "response_id": data.get("id")},
    )
    return text, {"sources": [str(x.id) for x in knowledge], "usage": usage, "response_id": data.get("id")}
