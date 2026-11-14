from celery import shared_task

from . import services


@shared_task(ignore_result=True)
def lapse_unpaid_appointments() -> int:
    return services.lapse_unpaid_appointments()


@shared_task(ignore_result=True)
def send_reminders() -> int:
    return services.send_reminders()
