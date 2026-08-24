from django.urls import path

from . import views
from .direct_uploads import views as direct_upload_views

app_name = "galleries_dashboard"

urlpatterns = [
    path("", views.gallery_list, name="list"),
    path("new/", views.gallery_create, name="create"),
    path("<uuid:gallery_uuid>/", views.gallery_detail, name="detail"),
    path("<uuid:gallery_uuid>/edit/", views.gallery_edit, name="edit"),
    path("<uuid:gallery_uuid>/access/", views.gallery_access_settings, name="access"),
    path("<uuid:gallery_uuid>/photos/", views.gallery_photos, name="photos"),
    path(
        "<uuid:gallery_uuid>/uploads/init/",
        direct_upload_views.upload_init,
        name="upload_init",
    ),
    path(
        "<uuid:gallery_uuid>/photos/upload/",
        views.gallery_photos_upload,
        name="photos_upload",
    ),
    path(
        "<uuid:gallery_uuid>/photos/status/",
        views.gallery_photos_status,
        name="photos_status",
    ),
    path(
        "<uuid:gallery_uuid>/photos/<uuid:photo_uuid>/retry/",
        views.gallery_photo_retry,
        name="photo_retry",
    ),
    path(
        "<uuid:gallery_uuid>/photos/<uuid:photo_uuid>/delete/",
        views.gallery_photo_delete,
        name="photo_delete",
    ),
    path(
        "<uuid:gallery_uuid>/photos/delete/",
        views.gallery_photos_bulk_delete,
        name="photos_bulk_delete",
    ),
    path(
        "<uuid:gallery_uuid>/photos/reorder/",
        views.gallery_photos_reorder,
        name="photos_reorder",
    ),
    path(
        "<uuid:gallery_uuid>/photos/<uuid:photo_uuid>/cover/",
        views.gallery_photo_set_cover,
        name="photo_cover",
    ),
    path("<uuid:gallery_uuid>/share/", views.gallery_share, name="share"),
    path("<uuid:gallery_uuid>/qr/", views.gallery_qr, name="qr"),
    path("<uuid:gallery_uuid>/publish/", views.gallery_publish, name="publish"),
    path("<uuid:gallery_uuid>/archive/", views.gallery_archive, name="archive"),
    path(
        "<uuid:gallery_uuid>/downloads/invalidate/",
        views.gallery_downloads_invalidate,
        name="downloads_invalidate",
    ),
]
