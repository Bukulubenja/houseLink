from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    path("", views.my_viewings, name="list"),
    path("request/<uuid:property_pk>/", views.request_viewing, name="request"),
    path("<uuid:pk>/", views.detail, name="detail"),
    path("<uuid:pk>/pay/", views.pay, name="pay"),
    path("<uuid:pk>/cancel/", views.cancel, name="cancel"),
    path("<uuid:pk>/feedback/", views.feedback, name="feedback"),
]
