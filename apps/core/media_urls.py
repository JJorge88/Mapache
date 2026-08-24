from django.urls import path

from . import media_views

app_name = "core_media"

urlpatterns = [
    path(
        "media/photos/<uuid:photo_uuid>/<str:variant>/",
        media_views.local_photo_media,
        name="local_photo",
    ),
    path(
        "g/<slug:slug>/media/<uuid:photo_uuid>/<str:variant>/<str:token>/",
        media_views.private_photo_media,
        name="private_photo",
    ),
]
