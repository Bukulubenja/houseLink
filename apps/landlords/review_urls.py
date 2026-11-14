from django.urls import path

from . import review_views

app_name = "review"

urlpatterns = [
    path("identity/", review_views.identity_queue, name="identity_queue"),
    path("identity/<uuid:pk>/", review_views.identity_detail, name="identity_detail"),
    path(
        "identity/<uuid:pk>/<str:kind>/",
        review_views.identity_document,
        name="identity_document",
    ),
]
