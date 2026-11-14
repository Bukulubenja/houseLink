import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.urls import reverse

from apps.accounts.models import User
from apps.core.roles import Role, has_role, roles_of

pytestmark = pytest.mark.django_db


def test_groups_exist_after_migrate():
    assert set(Group.objects.values_list("name", flat=True)) >= {r.value for r in Role}


def test_role_needs_staff_flag_and_group(make_staff, customer):
    staff = make_staff(Role.FINANCE)
    assert has_role(staff, Role.FINANCE)
    assert not has_role(staff, Role.BOOKINGS)
    assert roles_of(customer) == set()
    assert not has_role(AnonymousUser(), Role.FINANCE)

    staff.is_staff = False
    assert not has_role(staff, Role.FINANCE)  # a stale group membership is not enough


def test_inactive_staff_lose_roles(make_staff):
    staff = make_staff(Role.FINANCE)
    staff.is_active = False
    assert not has_role(staff, Role.FINANCE)


def test_superuser_holds_every_role():
    boss = User.objects.create_superuser("+256700000002", password="a-long-admin-password")
    assert roles_of(boss) == set(Role)


def test_ops_anonymous_goes_to_password_login(client):
    response = client.get(reverse("ops:dashboard"))
    assert response.status_code == 302
    assert reverse("admin:login") in response.url


def test_ops_hidden_from_customers(client, customer):
    client.force_login(customer)
    assert client.get(reverse("ops:dashboard")).status_code == 404


def test_nav_shows_ops_link_to_staff(client, make_staff):
    client.force_login(make_staff(Role.BOOKINGS))
    response = client.get(reverse("core:home"))
    assert reverse("ops:dashboard").encode() in response.content
