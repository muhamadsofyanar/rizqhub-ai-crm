# Changelog

## 2026-07-30 — Gemini provider update

- Menambahkan `AI_PROVIDER=auto|gemini|openai`.
- Menambahkan dukungan Gemini REST `generateContent` melalui `GEMINI_API_KEY`.
- Default model Gemini: `gemini-2.5-flash`.
- Mempertahankan kompatibilitas OpenAI Responses API.
- Menormalkan pencatatan token dan metadata provider pada `UsageRecord`.
- Memperbarui Docker Compose, `.env.example`, dashboard Agent Playground, README, dan panduan Coolify.
