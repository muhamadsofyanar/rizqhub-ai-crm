# Changelog

## 3.0.0 - Integrated pilot release

- Inbox Live polling tanpa refresh manual.
- Message retry, status delivery, dan deduplikasi webhook.
- Human handoff, assignment, note internal, dan approval draft AI.
- Knowledge revision dan evaluasi jawaban AI.
- Pipeline, automation, n8n webhook action, campaign WhatsApp/email.
- Workspace, membership, subscription foundation, audit log, system health.
- Celery Beat untuk scheduler dan backup PostgreSQL harian.
- Feature flags agar aktivasi produksi dapat dilakukan bertahap.

## 2026-07-30 — Gemini provider update

- Menambahkan `AI_PROVIDER=auto|gemini|openai`.
- Menambahkan dukungan Gemini REST `generateContent` melalui `GEMINI_API_KEY`.
- Default model Gemini: `gemini-2.5-flash`.
- Mempertahankan kompatibilitas OpenAI Responses API.
- Menormalkan pencatatan token dan metadata provider pada `UsageRecord`.
- Memperbarui Docker Compose, `.env.example`, dashboard Agent Playground, README, dan panduan Coolify.
