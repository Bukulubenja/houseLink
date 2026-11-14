import pytest
from django.test import RequestFactory

from apps.core import ratelimit
from apps.core.net import get_client_ip


def test_allow_permits_up_to_limit_then_blocks():
    results = [ratelimit.allow("k", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_limits_are_per_key():
    assert ratelimit.allow("a", limit=1, window_seconds=60)
    assert ratelimit.allow("b", limit=1, window_seconds=60)
    assert not ratelimit.allow("a", limit=1, window_seconds=60)


def test_cooldown_can_be_claimed_once():
    assert ratelimit.claim_cooldown("x", 60) is True
    assert ratelimit.claim_cooldown("x", 60) is False


@pytest.fixture
def rf():
    return RequestFactory()


def test_client_ip_ignores_forwarded_header_without_trusted_proxies(rf, settings):
    settings.NUM_PROXIES = 0
    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"


def test_client_ip_takes_the_hop_added_by_our_proxy_not_the_spoofed_one(rf, settings):
    settings.NUM_PROXIES = 1
    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 41.210.1.2", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(request) == "41.210.1.2"


def test_client_ip_falls_back_when_header_is_shorter_than_proxy_chain(rf, settings):
    settings.NUM_PROXIES = 2
    request = rf.get("/", HTTP_X_FORWARDED_FOR="41.210.1.2", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"
