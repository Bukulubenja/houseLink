from celery import shared_task

from .sms import SmsError, send_sms


@shared_task(
    autoretry_for=(SmsError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=4,
    ignore_result=True,
)
def send_sms_task(to: str, body: str) -> None:
    send_sms(to, body)
