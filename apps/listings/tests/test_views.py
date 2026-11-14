import pytest
from django.urls import reverse

from apps.listings import services

pytestmark = pytest.mark.django_db
LIST = reverse("listings:list")


def test_list_shows_only_published(client, published_property, acquirer, property_contact, details):
    services.create_property(
        actor=acquirer, contact=property_contact, details=details(title="Unchecked draft")
    )
    response = client.get(LIST)
    assert response.status_code == 200
    assert b"Two-bedroom apartment" in response.content
    assert b"Unchecked draft" not in response.content


@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("?area=Ntinda", True),
        ("?area=Naguru", False),
        ("?max_rent=800000", False),
        ("?min_rent=800000&bedrooms=2&kind=apartment", True),
        ("?bedrooms=3", False),
        ("?min_rent=-5", True),  # invalid filters are ignored, not an error
    ],
)
def test_filters(client, published_property, query, found):
    response = client.get(LIST + query)
    assert (b"Two-bedroom apartment" in response.content) is found


def test_detail_shows_what_was_checked_but_no_private_details(client, published_property):
    response = client.get(reverse("listings:detail", args=[published_property.pk]))
    assert response.status_code == 200
    assert b"What we checked" in response.content
    assert b"Rent and charges confirmed" in response.content
    assert b"Blue gate" not in response.content  # directions are for the broker only
    assert published_property.contact.phone.encode() not in response.content
    assert b"not a legal guarantee" in response.content


def test_detail_404_for_unpublished(client, draft_property):
    assert client.get(reverse("listings:detail", args=[draft_property.pk])).status_code == 404
