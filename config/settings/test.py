from .base import *  # noqa: F403
from .base import BASE_DIR, env

SECRET_KEY = "test-secret-key-not-for-production"  # noqa: S105
PII_HASH_KEY = "test-pii-key"
ALLOWED_HOSTS = ["testserver", "localhost"]

# CI sets DATABASE_URL to PostgreSQL and CACHE_URL to Redis; locally SQLite + locmem.
DATABASES = {"default": env.db("DATABASE_URL", default="sqlite://:memory:")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://")}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # speed only
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
SMS_BACKEND = "apps.core.sms.LocmemBackend"

MEDIA_ROOT = BASE_DIR / ".test_media"
PRIVATE_MEDIA_ROOT = BASE_DIR / ".test_private_media"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "private": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
SERVICE_AREAS = ["Ntinda", "Kiwatule", "Kigoowa", "Naguru"]
VIEWING_FEE_UGX = 44_000
PAYMENT_PROVIDER = "manual"
PAYMENT_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105
PAYMENT_MERCHANT_NUMBER = "0772000000"
SUPPORT_PHONE = "+256772000000"
LOGGING = {"version": 1, "disable_existing_loggers": False}
