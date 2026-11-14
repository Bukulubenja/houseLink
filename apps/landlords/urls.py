from django.urls import path

from . import views

app_name = "landlords"

urlpatterns = [
    path("verify/", views.verify_identity, name="verify"),
]
