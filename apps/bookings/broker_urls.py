from django.urls import path

from . import views

app_name = "broker"

urlpatterns = [
    path("", views.broker_home, name="home"),
    path("<uuid:pk>/", views.broker_appointment, name="appointment"),
]
