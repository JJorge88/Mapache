from django.urls import path

from . import download_views, views

app_name = "galleries_public"

urlpatterns = [
    path("portfolio/", views.portfolio, name="portfolio"),
    path("g/<slug:slug>/", views.public_gallery, name="detail"),
    path("g/<slug:slug>/access/", views.gallery_pin_access, name="access"),
    path(
        "g/<slug:slug>/photo/<uuid:photo_uuid>/download/",
        download_views.photo_download,
        name="photo_download",
    ),
    path(
        "g/<slug:slug>/download/",
        download_views.gallery_download_request,
        name="download_request",
    ),
    path(
        "g/<slug:slug>/download/<uuid:download_uuid>/status/",
        download_views.gallery_download_status,
        name="download_status",
    ),
    path(
        "g/<slug:slug>/download/<uuid:download_uuid>/prepare/",
        download_views.gallery_download_prepare,
        name="download_prepare",
    ),
    path(
        "g/<slug:slug>/download/<uuid:download_uuid>/",
        download_views.gallery_download_file,
        name="download_file",
    ),
]
