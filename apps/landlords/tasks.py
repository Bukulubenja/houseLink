from celery import shared_task

from . import services


@shared_task(ignore_result=True)
def purge_expired_selfies() -> int:
    return services.purge_expired_selfies()
