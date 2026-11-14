from unittest import mock

import pytest
from django.db import DatabaseError
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_home_renders(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert b"View it with confidence" in response.content
    assert b"No viewing fees" not in response.content


def test_security_headers_are_present(client):
    response = client.get(reverse("core:home"))
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_healthz_ok(client):
    response = client.get(reverse("core:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "no-cache" in response.headers["Cache-Control"]


def test_healthz_reports_unavailable_without_leaking_details(client):
    broken = mock.Mock()
    broken.cursor.side_effect = DatabaseError("secret")
    with mock.patch("apps.core.views.connection", broken):
        response = client.get(reverse("core:healthz"))
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert b"secret" not in response.content


def test_healthz_rejects_post(client):
    assert client.post(reverse("core:healthz")).status_code == 405
