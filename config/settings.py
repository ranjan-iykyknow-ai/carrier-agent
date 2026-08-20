"""Django settings, driven entirely by environment variables.

Local defaults match docker-compose.yml; production (Railway) overrides via env.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "apps.freight",
    "apps.comms",
    "apps.aiops",
    "apps.inquiries",
    "apps.candidates",
    "apps.workspace",
    "apps.accounts",
    "apps.dashboard",
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
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
                "apps.dashboard.context.shell",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://goodlane:goodlane@localhost:5432/carrier_agent",
    ),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# whitenoise emits a warning at startup if the directory does not exist yet
STATIC_ROOT.mkdir(exist_ok=True)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "var" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Celery ---
# Result backend stays disabled: job state lives on IngestionJob rows, never in
# transient task results. Tasks tolerate at-least-once delivery (acks_late) and
# receive only stable record UUIDs.
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TIMEZONE = "UTC"

# --- Demo clock ---
# Resolved once into DatasetSnapshot.as_of_at at seed time; application code
# reads the stored snapshot value, never this variable, after seeding.
DEMO_AS_OF_DATE = env("DEMO_AS_OF_DATE", default="2026-05-25")
DISPLAY_TIMEZONE = env("APP_TIME_ZONE", default="America/New_York")

# --- Providers ---
OPENAI_MODEL_EXTRACTION = env("OPENAI_MODEL_EXTRACTION", default="gpt-5.6-luna")
OPENAI_REASONING_EFFORT_EXTRACTION = env("OPENAI_REASONING_EFFORT_EXTRACTION", default="low")
DEEPGRAM_MODEL = env("DEEPGRAM_MODEL", default="nova-3")
DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY", default=None)
LANGFUSE_BASE_URL = env("LANGFUSE_BASE_URL", default=None)
LANGFUSE_PUBLIC_KEY = env("LANGFUSE_PUBLIC_KEY", default=None)
LANGFUSE_SECRET_KEY = env("LANGFUSE_SECRET_KEY", default=None)
LANGFUSE_CAPTURE_PAYLOADS = env.bool("LANGFUSE_CAPTURE_PAYLOADS", default=True)
PROVIDER_TIMEOUT_SECONDS = env.int("PROVIDER_TIMEOUT_SECONDS", default=60)

# --- Seed and ingestion limits ---
DATASET_VERSION = env("DATASET_VERSION", default="goodlane-v1")
SEED_MAX_WAV_BYTES = 25 * 1024 * 1024
SEED_MAX_WAV_SECONDS = 600
MAX_JOB_RETRIES = env.int("MAX_JOB_RETRIES", default=3)
# Weak name similarity proposes review candidates only; it can never verify.
WEAK_NAME_SIMILARITY_THRESHOLD = 0.6
# Transcript segments below this provider confidence are visibly uncertain.
TRANSCRIPT_LOW_CONFIDENCE_THRESHOLD = 0.7

# --- Stale-job sweep thresholds (seconds) ---
# Processing: generously above worker task time limits so a slow-but-alive
# worker is not swept (the duplicate-spend window is a recorded delta).
STALE_PROCESSING_SWEEP_SECONDS = env.int("STALE_PROCESSING_SWEEP_SECONDS", default=1800)
# Queued applies to manual-origin jobs only; deferred dataset jobs are excluded.
STALE_QUEUED_SWEEP_SECONDS = env.int("STALE_QUEUED_SWEEP_SECONDS", default=900)
