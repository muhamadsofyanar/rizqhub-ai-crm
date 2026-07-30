from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env(DJANGO_DEBUG=(bool, False))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-dev-key")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = [x.strip() for x in env("ALLOWED_HOSTS", default="*").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in env("CSRF_TRUSTED_ORIGINS", default="").split(",") if x.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crm",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "crm.middleware.CurrentTenantMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "crm.context_processors.app_context",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "id"
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = False

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 300,
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TIME_LIMIT = 180
CELERY_TASK_SOFT_TIME_LIMIT = 160
CELERY_TASK_TRACK_STARTED = True
CELERY_BEAT_SCHEDULE = {
    "run-due-campaigns": {
        "task": "crm.tasks.run_due_campaigns",
        "schedule": 60.0,
    },
    "scan-no-reply-automations": {
        "task": "crm.tasks.scan_no_reply_automations",
        "schedule": 60.0,
    },
}

APP_BASE_URL = env("APP_BASE_URL", default="http://localhost:8000").rstrip("/")
APP_ENCRYPTION_KEY = env("APP_ENCRYPTION_KEY", default="")
AI_PROVIDER = env("AI_PROVIDER", default="auto").strip().lower()
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-3.6-flash")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
OPENAI_MODEL = env("OPENAI_MODEL", default="gpt-5-mini")
AUTO_REPLY_DEFAULT = env.bool("AUTO_REPLY_DEFAULT", default=False)

FEATURE_LIVE_INBOX = env.bool("FEATURE_LIVE_INBOX", default=True)
FEATURE_MESSAGE_RETRY = env.bool("FEATURE_MESSAGE_RETRY", default=True)
FEATURE_AI_EVALUATION = env.bool("FEATURE_AI_EVALUATION", default=True)
FEATURE_AUTOMATION = env.bool("FEATURE_AUTOMATION", default=True)
FEATURE_CAMPAIGN = env.bool("FEATURE_CAMPAIGN", default=True)
FEATURE_SAAS = env.bool("FEATURE_SAAS", default=True)
FEATURE_BACKUP = env.bool("FEATURE_BACKUP", default=True)
LIVE_INBOX_POLL_SECONDS = env.float("LIVE_INBOX_POLL_SECONDS", default=2.5)
CAMPAIGN_MAX_RECIPIENTS = env.int("CAMPAIGN_MAX_RECIPIENTS", default=500)
WEBHOOK_MAX_BYTES = env.int("WEBHOOK_MAX_BYTES", default=1024 * 1024)
WEBHOOK_RATE_LIMIT_PER_MINUTE = env.int("WEBHOOK_RATE_LIMIT_PER_MINUTE", default=180)
BACKUP_DIR = env("BACKUP_DIR", default="/app/backups")
BACKUP_RETENTION_DAYS = env.int("BACKUP_RETENTION_DAYS", default=14)
BACKUP_HOUR = env.int("BACKUP_HOUR", default=2)
if FEATURE_BACKUP:
    CELERY_BEAT_SCHEDULE["daily-database-backup"] = {
        "task": "crm.tasks.create_scheduled_backup",
        "schedule": crontab(hour=BACKUP_HOUR, minute=0),
    }

# Existing installations were created with syncdb. New V3 tables are additive and
# are created safely by migrate --run-syncdb without altering existing tables.
MIGRATION_MODULES = {"crm": None}
