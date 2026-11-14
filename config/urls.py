from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Crib Link administration"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("homes/", include("apps.listings.urls")),
    path("viewings/", include("apps.bookings.urls")),
    path("broker/", include("apps.bookings.broker_urls")),
    path("payments/", include("apps.payments.urls")),
    path("ops/", include("apps.ops.urls")),
    path("landlord/", include("apps.landlords.urls")),
    path("review/", include("apps.landlords.review_urls")),
    path("", include("apps.core.urls")),
]
