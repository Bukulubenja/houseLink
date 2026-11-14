from celery import shared_task

from . import services


@shared_task(ignore_result=True)
def expire_overdue_listings() -> int:
    return services.expire_overdue_listings()
