# Arsitektur teknis

## Tenant isolation

Semua model bisnis memiliki `tenant_id`. Middleware memilih workspace aktif dari membership pengguna dan seluruh query UI membatasi data dengan `request.tenant`.

Untuk skala enterprise, tambahkan:

- PostgreSQL Row-Level Security.
- Test otomatis untuk cross-tenant access.
- Permission granular per action.
- Separate database untuk tenant tertentu bila dibutuhkan.

## Message processing

```text
StarSender
  -> POST webhook tokenized URL
  -> simpan WebhookEvent + SHA-256 payload
  -> HTTP 200 secepat mungkin
  -> Celery queue
  -> normalize contact
  -> create/open conversation
  -> store inbound message
  -> handoff check
  -> retrieval knowledge
  -> OpenAI Responses API
  -> draft atau send StarSender
```

## Idempotency

- Event unik berdasarkan tenant, provider, dan hash payload.
- Message premium dapat menggunakan `message_id` untuk deduplication tambahan.
- Outbound task menggunakan record Message sebagai unit kerja.

## AI safety

- Agent memiliki system prompt, mode, handoff keyword, dan knowledge terpisah.
- AI diminta hanya menjawab berdasarkan sumber bisnis.
- Mode default agent adalah Draft.
- Auto-reply global default dinonaktifkan.
- Legalitas, refund, komplain, dan informasi sensitif diarahkan ke manusia.

## Provider abstraction

Fungsi StarSender dan Mailketing berada pada `crm/services/providers.py`. Saat menambah Meta Cloud API atau provider lain, buat adapter baru dan hindari memasukkan detail provider ke model CRM inti.

## Scaling path

1. Satu VPS: web, worker, PostgreSQL, Redis.
2. Pisahkan PostgreSQL ke server/managed database.
3. Pisahkan worker dan Redis.
4. Tambahkan object storage.
5. Tambahkan beberapa worker queue berdasarkan jenis pekerjaan.
6. Tambahkan observability, tracing, alert, dan dead-letter queue.
