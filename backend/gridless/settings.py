"""Django settings for Gridless.

Auth is deliberately disabled in v1 (see the plan's "Security posture" section):
BOLT_DEFAULT_PERMISSION_CLASSES is [AllowAny()] and the organization is resolved
from the X-Org-Id header by core.deps.get_org. That header is spoofable — org
scoping is a correctness boundary here, not a security one. Turning auth on is a
change to the two BOLT_* settings below plus core.deps.get_org, nothing else.
"""

from pathlib import Path

from django_bolt import AllowAny, FileSize
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # required for GinIndex on Record.data
    "django_bolt",
    "core",
    "records",
    "ask",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "gridless.urls"
WSGI_APPLICATION = "gridless.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database -------------------------------------------------------------
# Port 5433 on the host to avoid colliding with a local Postgres on 5432.
DB_NAME = os.environ.get("POSTGRES_DB", "gridless")
DB_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.environ.get("POSTGRES_PORT", "5433")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": os.environ.get("POSTGRES_USER", "gridless"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "gridless"),
        "HOST": DB_HOST,
        "PORT": DB_PORT,
    }
}

# Connection string for the powerless role that /api/ask executes generated SQL
# as. Deliberately NOT a Django DATABASES entry — nothing should be able to
# reach it through the ORM. See ask/execute.py.
READONLY_DSN = os.environ.get(
    "GRIDLESS_READONLY_DSN",
    f"postgresql://gridless_ro:gridless_ro@{DB_HOST}:{DB_PORT}/{DB_NAME}",
)
ASK_STATEMENT_TIMEOUT_MS = int(os.environ.get("GRIDLESS_ASK_TIMEOUT_MS", "10000"))
ASK_MAX_ROWS = int(os.environ.get("GRIDLESS_ASK_MAX_ROWS", "500"))

# A relationship is accepted when the ranking stage's judgement of MEANING is at
# least this confident. Below it, the relationship is dropped rather than shown
# to the reviewer: the review screen is for non-technical users, and asking them
# to arbitrate a foreign key was asking the wrong question of the wrong person.
# Dropping loses a graph edge; accepting a wrong one corrupts every later answer.
# 0.85 rather than 0.90: the ranking stage judges a real lookup-table reference
# (Policy.Status -> Status Code.Status in the relational fixture) at 0.88, and
# losing it costs the cross-table answers that depend on the lookup. Everything
# genuinely coincidental in the fixtures still scores far below this.
RELATIONSHIP_ACCEPT_THRESHOLD = float(
    os.environ.get("GRIDLESS_RELATIONSHIP_THRESHOLD", "0.85")
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Django-Bolt ----------------------------------------------------------
BOLT_AUTHENTICATION_CLASSES: list = []
BOLT_DEFAULT_PERMISSION_CLASSES = [AllowAny()]
BOLT_MAX_UPLOAD_SIZE = FileSize.MB_50
BOLT_MEMORY_SPOOL_THRESHOLD = 2 * 1024 * 1024

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type", "X-Org-Id"]

# --- LLM (OpenRouter) -----------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "z-ai/glm-5.3-flash")
# NOTE: milliseconds. ChatOpenRouter.request_timeout maps to the OpenRouter
# SDK's timeout_ms, so a value that looks like "180 seconds" is 0.18s, every
# request times out instantly, and the SDK retries with backoff forever --
# which presents as a hang, not an error.
OPENROUTER_TIMEOUT_MS = int(os.environ.get("OPENROUTER_TIMEOUT_MS", "180000"))
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "8000"))
# GLM 5.3 Flash is a reasoning model and reasoning CANNOT be disabled
# ("Reasoning is mandatory for this endpoint"). Left unbounded it spends the
# whole token budget thinking and returns content: null. Low effort is correct
# for these stages: the judgement is constrained by the skill and the tools, not
# by how long the model deliberates.
OPENROUTER_REASONING_EFFORT = os.environ.get("OPENROUTER_REASONING_EFFORT", "low")
