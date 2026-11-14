"""Settings shared by every environment. Secrets and hosts come from the environment."""

from pathlib import Path

import environ
from celery.schedules import crontab
from django.utils.csp import CSP

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

# Required in prod (enforced in prod.py); dev/test provide throwaway defaults.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
PII_HASH_KEY = env("PII_HASH_KEY", default="")  # HMAC key for NIN / blocklist hashes

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.moderation",
    "apps.landlords",
    "apps.listings",
    "apps.brokers",
    "apps.bookings",
    "apps.payments",
    "apps.ops",
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
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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
                "django.template.context_processors.csp",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

# --- Auth -------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

PASSWORD_HASHERS = [  # only staff have passwords; tenants/landlords use SMS codes
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Sessions, cookies, headers --------------------------------------------
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30  # 30 days: phones on expensive data, re-login costs SMS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

# Number of reverse proxies in front of Django whose X-Forwarded-For we trust.
NUM_PROXIES = env.int("NUM_PROXIES", default=0)

# --- i18n -------------------------------------------------------------------
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

# --- Static & media ---------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"  # never served by a URL route

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {
            "location": PRIVATE_MEDIA_ROOT,
            "base_url": None,
            "file_permissions_mode": 0o600,
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="memory://")
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_TIME_LIMIT = 60
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "purge-expired-selfies": {
        "task": "apps.landlords.tasks.purge_expired_selfies",
        "schedule": crontab(hour=3, minute=0),
    },
    "expire-unrechecked-listings": {
        "task": "apps.listings.tasks.expire_overdue_listings",
        "schedule": crontab(minute=15),
    },
    "lapse-unpaid-appointments": {
        "task": "apps.bookings.tasks.lapse_unpaid_appointments",
        "schedule": crontab(minute="*/10"),
    },
    "send-appointment-reminders": {
        "task": "apps.bookings.tasks.send_reminders",
        "schedule": crontab(minute="*/15"),
    },
}

# --- Managed viewing service (business plan, 13 Nov 2026) ------------------
# Every value below is a working assumption from the plan, not a final decision.
# Pilot service area: Ntinda and a tight ring of neighbours. Exact boundary still open.
SERVICE_AREAS = env.list("SERVICE_AREAS", default=["Ntinda", "Kiwatule", "Kigoowa", "Naguru"])
# Charged per appointment; one property per appointment at launch. About US$12.
VIEWING_FEE_UGX = env.int("VIEWING_FEE_UGX", default=44_000)
LISTING_RECHECK_DAYS = 14  # a published listing expires unless re-verified within this time
LISTING_PHOTO_MAX_BYTES = 8 * 1024 * 1024
LISTING_MAX_PHOTOS = 12
APPOINTMENT_SLOT_MINUTES = 60  # a broker cannot hold two appointments inside this window
APPOINTMENT_MIN_LEAD_HOURS = 3  # earliest a customer can ask for
APPOINTMENT_MAX_LEAD_DAYS = 21
PAYMENT_WINDOW_HOURS = 12  # a confirmed slot is released if unpaid by then
PAYMENT_CLOSE_BEFORE_START_HOURS = 2  # ...or this long before the start, whichever is sooner
CANCELLATION_REFUND_CUTOFF_HOURS = 3  # customer cancels later than this: no refund
REMINDER_BEFORE_START_HOURS = 2
MAX_OPEN_REQUESTS_PER_CUSTOMER = 3
VIEWING_REQUESTS_PER_DAY = 10  # per customer, rate-limited

# Payments. "manual": the customer pays the business mobile-money number and finance records
# the provider's transaction ID after checking the merchant statement. Automated providers
# confirm through a signed webhook. A browser redirect never marks a payment as paid.
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="manual")
PAYMENT_WEBHOOK_SECRET = env("PAYMENT_WEBHOOK_SECRET", default="")
PAYMENT_WEBHOOK_TOLERANCE_SECONDS = 300
PAYMENT_MERCHANT_NUMBER = env("PAYMENT_MERCHANT_NUMBER", default="")
SUPPORT_PHONE = env("SUPPORT_PHONE", default="")

# --- SMS & one-time codes ---------------------------------------------------
SMS_BACKEND = "apps.core.sms.ConsoleBackend"
AFRICASTALKING_USERNAME = env("AFRICASTALKING_USERNAME", default="")
AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", default="")
AFRICASTALKING_SENDER_ID = env("AFRICASTALKING_SENDER_ID", default="")
AFRICASTALKING_SANDBOX = env.bool("AFRICASTALKING_SANDBOX", default=True)

ALLOWED_PHONE_REGIONS = ["UG"]  # blocks SMS toll fraud to foreign numbers
OTP_LENGTH = 6
OTP_TTL_SECONDS = 5 * 60
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_PER_PHONE_PER_HOUR = 5
# Generous because Ugandan mobile carriers put many subscribers behind one IP (CGNAT);
# the per-phone limits and UG-only rule are the main SMS-abuse controls.
OTP_MAX_PER_IP_PER_HOUR = 60

# --- Uploads & identity verification ---------------------------------------
DATA_UPLOAD_MAX_NUMBER_FILES = 10
IMAGE_MAX_PIXELS = 40_000_000  # ~40 MP; larger is a decompression-bomb risk
IMAGE_MAX_SIDE = 2000  # stored photos are resized to fit 2000x2000
IDENTITY_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
IDENTITY_REQUEST_MAX_BYTES = 30 * 1024 * 1024
IDENTITY_CONSENT_VERSION = "2026-11-12"
IDENTITY_SELFIE_RETENTION_DAYS = 90
IDENTITY_REVIEW_TARGET_HOURS = 24

ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
