import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# `manage.py test` always includes 'test' in argv for this project's
# convention (there is no pytest/conftest layer). Used below to disable
# DRF rate limiting during the test run: DRF's throttle counters live in
# Django's cache framework, which is process-global and NOT reset between
# individual test methods — a whole-suite run makes far more requests to
# e.g. the login endpoint than any real client would in a minute, so
# without this a tight production rate trips partway through the suite and
# cascades into unrelated failures for the rest of the run.
TESTING = 'test' in sys.argv


def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None or val == '':
        return default
    return val.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_list(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


# ---------------------------------------------------------------------------
# Environment identity
#
# ENVIRONMENT is the single switch that separates development from
# staging/production. It is intentionally NOT the same axis as DEBUG: DEBUG
# is a Django behavior flag, ENVIRONMENT is a deployment-topology flag that
# also governs which security defaults are safe to assume. `staging` and
# `production` share the same strict posture (both are real, internet-facing
# deployments); only `development` (and Django's own `test` runner, which
# never reads this at all) gets permissive defaults.
# ---------------------------------------------------------------------------
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'development').strip().lower()
if ENVIRONMENT not in ('development', 'staging', 'production'):
    raise ImproperlyConfigured(
        f"ENVIRONMENT={ENVIRONMENT!r} is not one of 'development', 'staging', 'production'."
    )
IS_PRODUCTION_LIKE = ENVIRONMENT in ('staging', 'production')

DEBUG = _env_bool('DEBUG', default=not IS_PRODUCTION_LIKE)
if IS_PRODUCTION_LIKE and DEBUG:
    raise ImproperlyConfigured(
        'DEBUG=1 is not allowed when ENVIRONMENT is staging or production. '
        'Verbose exception pages would leak internals to the internet.'
    )

# ---------------------------------------------------------------------------
# Secret key
#
# DJANGO_SECRET_KEY is the documented production name; SECRET_KEY is kept as
# a fallback for the existing local docker-compose.yml / developer .env
# files that already set it. Production/staging must supply a real one —
# there is no safe default to fall back to there.
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION_LIKE:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY must be set when ENVIRONMENT is staging or production. '
            'Generate one with scripts/generate-secrets.sh.'
        )
    SECRET_KEY = 'insecure-development-only-secret-key-do-not-use-outside-development'  # noqa: S105

# ---------------------------------------------------------------------------
# Hosts / CSRF / CORS
#
# The previous defaults (`ALLOWED_HOSTS = ['*']`, `CORS_ALLOW_ALL_ORIGINS =
# True`) were fine for a machine nobody else could reach, but they are not
# safe to carry into staging/production, where they'd let any Host header or
# any origin talk to the API. Both are now explicit, comma-separated
# allowlists read from the environment; staging/production refuse to start
# with an empty or wildcard list.
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', default='*' if not IS_PRODUCTION_LIKE else '')
if IS_PRODUCTION_LIKE and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        'ALLOWED_HOSTS must be a non-empty, non-wildcard comma-separated list of hostnames '
        'when ENVIRONMENT is staging or production (e.g. "chat-staging.rastisi.ir").'
    )
# backend/Dockerfile.prod's own HEALTHCHECK (and docker-compose.staging.yml's
# depends_on: backend: condition: service_healthy, which operator-dashboard/
# platform-dashboard/automation-worker/sla-worker all rely on) curls
# http://127.0.0.1:PORT/api/v1/health/live/ from *inside* the container —
# that request's Host header is literally "127.0.0.1:8000", which the real
# ALLOWED_HOSTS above (correctly locked to the real external domain) would
# otherwise reject with DisallowedHost, permanently failing the
# container's own healthcheck. Only reachable from inside the container/
# Docker network or the host's own loopback (the published port only binds
# 127.0.0.1, never 0.0.0.0 — see docker-compose.staging.yml), so allowing it
# doesn't extend what the public internet can reach.
ALLOWED_HOSTS = list(ALLOWED_HOSTS) + ['127.0.0.1', 'localhost']

CSRF_TRUSTED_ORIGINS = _env_list('CSRF_TRUSTED_ORIGINS')
if IS_PRODUCTION_LIKE and not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured(
        'CSRF_TRUSTED_ORIGINS must be set when ENVIRONMENT is staging or production '
        '(e.g. "https://operator-chat-staging.rastisi.ir,https://platform-chat-staging.rastisi.ir").'
    )

CORS_ALLOWED_ORIGINS = _env_list('CORS_ALLOWED_ORIGINS')
if IS_PRODUCTION_LIKE and not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured(
        'CORS_ALLOWED_ORIGINS must be set when ENVIRONMENT is staging or production. '
        'The Widget is embedded on arbitrary third-party pages, so this must list every '
        'domain allowed to call the API from a browser — it is not a wildcard.'
    )
# The Widget script itself is loaded via a plain <script> tag on third-party
# pages, not fetched cross-origin by the backend — CORS only governs
# browser-side fetch()/XHR calls the Widget/dashboards make back to this
# API, so this list is exactly "every frontend origin", nothing more.
if not IS_PRODUCTION_LIKE and not CORS_ALLOWED_ORIGINS:
    CORS_ALLOW_ALL_ORIGINS = True
else:
    CORS_ALLOW_ALL_ORIGINS = False

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'corsheaders',
    'channels',

    # Local Apps
    'accounts',
    'platforms',
    'workspaces',
    'projects',
    'visitors',
    'conversations',
    'catalog',
    'customer_context',
    'audit',
    'common',
    'teams',
    'queues',
    'sla',
    'collaboration',
    'notifications',
    'automations',
    'knowledge_base',
    'macros',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'common.middleware.RequestIDMiddleware',
    'common.middleware.SecurityHeadersMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
#
# DATABASE_URL (standard `postgres://user:pass@host:port/name` form) takes
# priority when set, matching common hosting-platform conventions; the
# original DB_NAME/DB_USER/... variables remain supported underneath it for
# the existing dev docker-compose.yml and any script that already sets them.
# SQLite is never used outside the `development` default — staging and
# production always require an explicit DATABASE_URL or DB_* set pointing
# at a real Postgres instance (the check below just enforces that a host
# other than the bare local default was actually configured).
# ---------------------------------------------------------------------------
_database_url = os.environ.get('DATABASE_URL')
if _database_url:
    DATABASES = {'default': dj_database_url.parse(_database_url, conn_max_age=60)}
else:
    if IS_PRODUCTION_LIKE and not os.environ.get('DB_HOST'):
        raise ImproperlyConfigured(
            'DATABASE_URL (or DB_HOST/DB_NAME/DB_USER/DB_PASSWORD) must be set when '
            'ENVIRONMENT is staging or production.'
        )
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'rastichat_db'),
            'USER': os.environ.get('DB_USER', 'rastichat'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'rastichat_secret'),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', 60)),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))
MEDIA_URL = '/media/'
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', str(BASE_DIR / 'media'))
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024  # 8MB cap for chat attachments (images/voice notes)
# Bounds the total request body Django will parse before rejecting it
# outright, so an oversized POST can't tie up a worker before the
# attachment-specific validation in conversations/media_validation.py ever
# runs. Nginx enforces the same ceiling at the proxy (client_max_body_size,
# see deploy/nginx) — this is the app-level backstop for anything reaching
# Daphne directly (e.g. local dev with no proxy in front of it).
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('DATA_UPLOAD_MAX_MEMORY_SIZE', 20 * 1024 * 1024))

# Enforced upper bounds for validated chat attachments (see conversations/media_validation.py).
MEDIA_UPLOAD_MAX_IMAGE_BYTES = int(os.environ.get('MEDIA_UPLOAD_MAX_IMAGE_BYTES', 8 * 1024 * 1024))
MEDIA_UPLOAD_MAX_VOICE_BYTES = int(os.environ.get('MEDIA_UPLOAD_MAX_VOICE_BYTES', 15 * 1024 * 1024))
# Dotted path to an optional `callable(file) -> bool` malware-scan hook. Unset by default.
MEDIA_UPLOAD_SCAN_HOOK = os.environ.get('MEDIA_UPLOAD_SCAN_HOOK', '')

# ---------------------------------------------------------------------------
# Redis (Channels layer + health check)
#
# REDIS_URL (`redis://[:password@]host:port/db`) takes priority; REDIS_HOST
# remains supported on its own for the existing dev docker-compose.yml.
# REDIS_URL is parsed once here into (host, port, password, db) so both the
# Channels layer config below and common.views.HealthCheckView's real Redis
# ping (as opposed to Django's default in-process LocMemCache, which was
# what the old health check was silently exercising) use the identical,
# single source of truth.
# ---------------------------------------------------------------------------
_redis_url = os.environ.get('REDIS_URL')
if _redis_url:
    from urllib.parse import urlparse
    _parsed_redis = urlparse(_redis_url)
    REDIS_CONNECTION = {
        'host': _parsed_redis.hostname or '127.0.0.1',
        'port': _parsed_redis.port or 6379,
        'password': _parsed_redis.password or None,
        'db': int((_parsed_redis.path or '/0').lstrip('/') or 0),
    }
else:
    if IS_PRODUCTION_LIKE and not os.environ.get('REDIS_HOST'):
        raise ImproperlyConfigured(
            'REDIS_URL (or REDIS_HOST) must be set when ENVIRONMENT is staging or production.'
        )
    REDIS_CONNECTION = {
        'host': os.environ.get('REDIS_HOST', '127.0.0.1'),
        'port': int(os.environ.get('REDIS_PORT', 6379)),
        'password': os.environ.get('REDIS_PASSWORD') or None,
        'db': int(os.environ.get('REDIS_DB', 0)),
    }

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            # channels_redis.utils.decode_hosts() only special-cases plain
            # (host, port) tuples/lists — anything else in this list must
            # already be the final per-connection kwargs dict it hands to
            # redis.asyncio.ConnectionPool(**host) (host/port/password/db),
            # NOT an "address" URL string (that key means something
            # different: create_pool() special-cases "address" as a full
            # redis:// URL for aioredis.ConnectionPool.from_url(), which a
            # bare tuple is not).
            "hosts": [{
                "host": REDIS_CONNECTION['host'],
                "port": REDIS_CONNECTION['port'],
                **({"password": REDIS_CONNECTION['password']} if REDIS_CONNECTION['password'] else {}),
                **({"db": REDIS_CONNECTION['db']} if REDIS_CONNECTION['db'] else {}),
            }],
        },
    },
}

# WebSocket-side rate limit for visitor chat messages (see
# common/ws_throttling.py and conversations/consumers.py:WidgetChatConsumer)
# — DRF's ScopedRateThrottle below only ever runs on the REST cycle and has
# no reach into a Channels consumer's receive_json.
WIDGET_WS_MESSAGE_RATE_LIMIT = int(os.environ.get('WIDGET_WS_MESSAGE_RATE_LIMIT', 30))
WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS = int(os.environ.get('WIDGET_WS_MESSAGE_RATE_WINDOW_SECONDS', 60))

# Matches the same-named values docker-compose.staging.yml passes to
# docker-scheduler-loop.sh — common.views.SchedulerStatusView uses these to
# judge whether a scheduler's last recorded heartbeat (see
# common/models.py:SchedulerHeartbeat) is stale, so keep them in sync with
# the actual loop interval rather than hardcoding a guess here.
AUTOMATION_WORKER_INTERVAL_SECONDS = int(os.environ.get('AUTOMATION_WORKER_INTERVAL_SECONDS', 30))
SLA_WORKER_INTERVAL_SECONDS = int(os.environ.get('SLA_WORKER_INTERVAL_SECONDS', 60))

# Read by common.views.MonitoringView's backup-freshness check and by
# scripts/staging/backup.sh (the actual writer) — same path, single source
# of truth. BACKUP_MAX_AGE_HOURS is deliberately generous (a bit more than
# 24h) so a backup that's merely running a little late doesn't page anyone;
# see docs/runbooks/MONITORING_RUNBOOK.md for the real alerting threshold.
BACKUP_DIR = os.environ.get('BACKUP_DIR', str(BASE_DIR / 'backups'))
BACKUP_MAX_AGE_HOURS = int(os.environ.get('BACKUP_MAX_AGE_HOURS', 26))
DISK_USAGE_WARNING_PERCENT = int(os.environ.get('DISK_USAGE_WARNING_PERCENT', 85))

# Shared-secret header (X-Monitoring-Token) common.views.MonitoringView
# requires before returning anything — disk usage, backup filenames/ages,
# and scheduler heartbeat status are operational details, not something an
# anonymous internet caller should see (unlike LivenessView/ReadinessView,
# which orchestrators/load balancers poll unauthenticated by design and
# stay that way). Required in staging/production, same fail-fast pattern
# as DJANGO_SECRET_KEY — generate with scripts/generate-secrets.sh.
MONITORING_TOKEN = os.environ.get('MONITORING_TOKEN')
if IS_PRODUCTION_LIKE and not MONITORING_TOKEN:
    raise ImproperlyConfigured(
        'MONITORING_TOKEN must be set when ENVIRONMENT is staging or production — '
        '/api/v1/health/monitoring/ would otherwise be unauthenticated. '
        'Generate one with scripts/generate-secrets.sh.'
    )

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# ---------------------------------------------------------------------------
# Admin URL
#
# Configurable so a staging/production deploy can move the admin off the
# well-known `/admin/` path to cut down automated scanner noise, without any
# code change — just ADMIN_URL in the environment file.
# ---------------------------------------------------------------------------
ADMIN_URL = os.environ.get('ADMIN_URL', 'admin/').strip('/') + '/'

# DRF Settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # Deliberately NOT set globally: conversations/views.py's
    # CustomerConversationViewSet declares `throttle_scope = 'media_upload'`
    # at the class level but relies on ScopedRateThrottle only actually
    # being consulted on its `upload` action (which opts in explicitly via
    # its own `throttle_classes`) — every other action on that ViewSet is
    # untouched. A global default here would silently pull every one of
    # those other actions under the same 'media_upload' budget. Each
    # throttled endpoint below opts in explicitly with its own
    # `throttle_classes = [ScopedRateThrottle]` instead.
    # `None` is DRF's own documented way to disable a scope (SimpleRateThrottle
    # .allow_request short-circuits to True when self.rate is None) — used
    # here to turn every scope off under TESTING rather than maintaining a
    # second, parallel "are we testing" branch DRF doesn't already support.
    'DEFAULT_THROTTLE_RATES': {
        # Applies per-user (operators) or per-IP (visitors, who are anonymous
        # to DRF) on the image/voice upload endpoints only.
        'media_upload': None if TESTING else os.environ.get('MEDIA_UPLOAD_THROTTLE_RATE', '30/min'),
        # Deliberately tight and IP-scoped — a login endpoint is the classic
        # credential-stuffing target.
        'login': None if TESTING else os.environ.get('LOGIN_THROTTLE_RATE', '10/min'),
        'widget_start': None if TESTING else os.environ.get('WIDGET_START_THROTTLE_RATE', '20/min'),
        'widget_message': None if TESTING else os.environ.get('WIDGET_MESSAGE_THROTTLE_RATE', '60/min'),
        'widget_rating': None if TESTING else os.environ.get('WIDGET_RATING_THROTTLE_RATE', '20/min'),
        'kb_feedback': None if TESTING else os.environ.get('KB_FEEDBACK_THROTTLE_RATE', '20/min'),
        'kb_search': None if TESTING else os.environ.get('KB_SEARCH_THROTTLE_RATE', '60/min'),
        'macro_execution': None if TESTING else os.environ.get('MACRO_EXECUTION_THROTTLE_RATE', '60/min'),
    },
}

# JWT Settings
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
}

# ---------------------------------------------------------------------------
# Production security hardening
#
# All gated on IS_PRODUCTION_LIKE — staging is a real internet-facing HTTPS
# deployment, not a trusted-network sandbox, so it gets the same posture as
# production rather than a weaker "staging" tier. Nothing here changes
# `development` behavior (all these settings keep Django's own defaults,
# which are already correct for a plain-HTTP localhost workflow).
# ---------------------------------------------------------------------------
if IS_PRODUCTION_LIKE:
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', default=True)
    # Nginx terminates TLS and forwards X-Forwarded-Proto; without this,
    # Django would see every request as plain HTTP (coming from Nginx over
    # the loopback/internal network) and loop-redirect forever.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    USE_X_FORWARDED_PORT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_HTTPONLY = True

    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', 31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', default=False)

    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
    # The API/admin are never legitimately framed by anyone (the Widget is a
    # <script> embed, not an iframe embed — this does not affect it).
    X_FRAME_OPTIONS = 'DENY'
else:
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    X_FRAME_OPTIONS = 'DENY'

# security.W021 ("SECURE_HSTS_PRELOAD is not True") fires precisely when
# SECURE_HSTS_PRELOAD is the deliberate default above — submitting to the
# browser preload list is a one-way, hard-to-reverse decision an operator
# opts into via SECURE_HSTS_PRELOAD=1, not something that should ever be a
# blocking condition. Silenced so `manage.py check --deploy --fail-level
# WARNING` (scripts/staging/deploy.sh's deploy-time security gate) can
# treat WARNING as "block the deploy" without this permanently-present,
# already-accounted-for advisory tripping it on every single deploy.
SILENCED_SYSTEM_CHECKS = ['security.W021']

# Content-Security-Policy for the handful of HTML pages Django itself
# renders (admin, DRF's browsable API, error pages) — applied via
# common.middleware.SecurityHeadersMiddleware rather than Django's own CSP
# support, which only landed in Django 5.1 (this project is pinned to 4.2).
# Deliberately conservative: this backend serves no first-party JS/CSS
# beyond what django.contrib.admin ships, so a same-origin-only policy
# doesn't break anything real.
CONTENT_SECURITY_POLICY = os.environ.get(
    'CONTENT_SECURITY_POLICY',
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
)

# ---------------------------------------------------------------------------
# Logging
#
# Everything goes to stdout/stderr (12-factor: the container runtime/journald
# owns log storage and rotation, this process never writes log files of its
# own). No formatter here ever includes request bodies, Authorization
# headers, JWTs, or passwords — only what call sites explicitly pass to
# `logger.info(...)`, and every call site in this codebase that logs a
# denial/failure logs a summary, never the raw credential.
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get('DJANGO_LOG_LEVEL', 'INFO').upper()
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'common.middleware.RequestIDLogFilter'},
    },
    'formatters': {
        'verbose': {
            'format': '%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'filters': ['request_id'],
        },
    },
    'root': {
        # Deliberately NOT LOG_LEVEL: that would also drop every
        # third-party library (asyncio, urllib3, ...) to DEBUG in
        # development, drowning out this app's own logs. Only the loggers
        # below — this codebase's own — follow LOG_LEVEL.
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        # django.server / daphne access logs are already single-line and
        # secret-free (method, path, status, latency) — kept at the same
        # level as everything else rather than silenced.
        'rastichat': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
    },
}
