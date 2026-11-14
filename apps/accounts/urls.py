from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("sign-in/", views.phone_step, name="login"),
    path("sign-in/code/", views.code_step, name="code"),
    path("sign-out/", views.logout_view, name="logout"),
]
