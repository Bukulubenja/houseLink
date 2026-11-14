from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("dev/<str:reference>/", views.dev_checkout, name="dev_checkout"),
]
