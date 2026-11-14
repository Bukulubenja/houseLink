from .base import *  # noqa: F403
from .base import PII_HASH_KEY, SECRET_KEY, env

DEBUG = True
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

SECRET_KEY = SECRET_KEY or "dev-only-insecure-secret-key-change-me"
PII_HASH_KEY = PII_HASH_KEY or "dev-only-insecure-pii-key"

CELERY_TASK_ALWAYS_EAGER = True
SMS_BACKEND = "apps.core.sms.ConsoleBackend"  # codes are printed in the runserver console
