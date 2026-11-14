import io
import json
from unittest import mock

import pytest

from apps.core import sms
from apps.core.tasks import send_sms_task


def test_send_sms_uses_configured_backend(sms_outbox):
    sms.send_sms("+256772123456", "hello")
    assert sms_outbox == [sms.SmsMessage(to="+256772123456", body="hello")]


def test_send_sms_task_delivers_through_backend(sms_outbox):
    send_sms_task.delay("+256772123456", "queued")
    assert sms_outbox[0].body == "queued"


def test_console_backend_logs_message(caplog):
    sms.ConsoleBackend().send(sms.SmsMessage(to="+256772123456", body="code 1"))
    assert "code 1" in caplog.text


def _fake_response(payload: dict):
    response = io.BytesIO(json.dumps(payload).encode())
    response.__enter__ = lambda self=response: self
    response.__exit__ = lambda *args: None
    return response


@pytest.fixture
def at_settings(settings):
    settings.AFRICASTALKING_USERNAME = "sandbox"
    settings.AFRICASTALKING_API_KEY = "key"
    settings.AFRICASTALKING_SENDER_ID = "CRIBLINK"
    settings.AFRICASTALKING_SANDBOX = True
    return settings


def test_africastalking_success_posts_to_sandbox(at_settings):
    ok = {"SMSMessageData": {"Recipients": [{"status": "Success"}]}}
    with mock.patch("urllib.request.urlopen", return_value=_fake_response(ok)) as urlopen:
        sms.AfricasTalkingBackend().send(sms.SmsMessage(to="+256772123456", body="hi"))
    request = urlopen.call_args.args[0]
    assert request.full_url == sms.AfricasTalkingBackend.SANDBOX_URL
    assert request.get_header("Apikey") == "key"
    assert b"from=CRIBLINK" in request.data


def test_africastalking_rejected_recipient_raises(at_settings):
    bad = {"SMSMessageData": {"Recipients": [{"status": "InvalidPhoneNumber"}]}}
    with mock.patch("urllib.request.urlopen", return_value=_fake_response(bad)):
        with pytest.raises(sms.SmsError, match="InvalidPhoneNumber"):
            sms.AfricasTalkingBackend().send(sms.SmsMessage(to="+256772123456", body="hi"))


def test_africastalking_network_failure_raises_sms_error(at_settings):
    with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        with pytest.raises(sms.SmsError, match="timeout"):
            sms.AfricasTalkingBackend().send(sms.SmsMessage(to="+256772123456", body="hi"))
