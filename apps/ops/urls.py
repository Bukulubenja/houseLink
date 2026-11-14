from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("listings/", views.property_list, name="property_list"),
    path("listings/new/", views.property_new, name="property_new"),
    path("listings/contacts/new/", views.contact_new, name="contact_new"),
    path("listings/<uuid:pk>/", views.property_detail, name="property_detail"),
    path("listings/<uuid:pk>/edit/", views.property_edit, name="property_edit"),
    path("listings/<uuid:pk>/authority/", views.authority_document, name="authority_document"),
    path("bookings/", views.booking_queue, name="booking_queue"),
    path("bookings/<uuid:pk>/", views.booking_detail, name="booking_detail"),
    path("brokers/", views.broker_list, name="broker_list"),
    path("brokers/<uuid:pk>/status/", views.broker_status, name="broker_status"),
    path("finance/", views.finance, name="finance"),
    path("finance/payments/record/", views.record_payment, name="record_payment"),
    path(
        "finance/payments/<uuid:pk>/reconcile/", views.reconcile_payment, name="reconcile_payment"
    ),
    path("finance/payments/<uuid:pk>/resolve/", views.resolve_payment, name="resolve_payment"),
    path("finance/refunds/<uuid:pk>/pay/", views.pay_refund, name="pay_refund"),
]
