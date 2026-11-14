from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import DATABASES, PII_HASH_KEY, SECRET_KEY, env

for name, value in {"DJANGO_SECRET_KEY": SECRET_KEY, "PII_HASH_KEY": PII_HASH_KEY}.items():
    if len(value) < 32:
        raise ImproperlyConfigured(f"{name} must be set to a random value of 32+ characters.")
if SECRET_KEY == PII_HASH_KEY:
    raise ImproperlyConfigured("PII_HASH_KEY must differ from DJANGO_SECRET_KEY.")
if not DATABASES["default"]["ENGINE"].endswith("postgresql"):
    raise ImproperlyConfigured("Production requires PostgreSQL (DATABASE_URL=postgres://...).")

DEBUG = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# --- HTTPS ------------------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]
if env.bool("BEHIND_TLS_PROXY", default=True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Without this every user appears to share the proxy's IP and one rate-limit bucket.
    NUM_PROXIES = env.int("NUM_PROXIES", default=1)
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# --- Celery / SMS -----------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
SMS_BACKEND = env("SMS_BACKEND", default="apps.core.sms.AfricasTalkingBackend")
if SMS_BACKEND in {"apps.core.sms.ConsoleBackend", "apps.core.sms.LocmemBackend"}:
    raise ImproperlyConfigured("Console/locmem SMS backends would log sign-in codes in production.")

# --- Payments ---------------------------------------------------------------
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="manual")
if PAYMENT_PROVIDER == "dev":
    raise ImproperlyConfigured("The dev payment provider simulates payments; never in production.")
if PAYMENT_PROVIDER == "manual" and not env("PAYMENT_MERCHANT_NUMBER", default=""):
    raise ImproperlyConfigured("Manual payments need PAYMENT_MERCHANT_NUMBER.")
if PAYMENT_PROVIDER != "manual" and len(env("PAYMENT_WEBHOOK_SECRET", default="")) < 32:
    raise ImproperlyConfigured("PAYMENT_WEBHOOK_SECRET must be 32+ random characters.")

# --- Object storage: public bucket for listing photos, private for ID docs ---
_s3_common = {
    "endpoint_url": env("S3_ENDPOINT_URL", default=None),
    "access_key": env("S3_ACCESS_KEY_ID"),
    "secret_key": env("S3_SECRET_ACCESS_KEY"),
    "region_name": env("S3_REGION", default="auto"),
    "file_overwrite": False,
}
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {**_s3_common, "bucket_name": env("S3_PUBLIC_BUCKET")},
    },
    "private": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            **_s3_common,
            "bucket_name": env("S3_PRIVATE_BUCKET"),
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": 300,  # signed links live 5 minutes
        },
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.security": {"level": "WARNING", "propagate": True}},
}
