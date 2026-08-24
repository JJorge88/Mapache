from django.contrib import admin
from django.urls import include, path

from apps.mapache_ai import views as mapache_ai_views

urlpatterns = [
    path("", include("apps.core.media_urls", namespace="core_media")),
    path("admin/", admin.site.urls),
    path(
        "dashboard/galleries/",
        include("apps.galleries.dashboard_urls", namespace="galleries_dashboard"),
    ),
    path(
        "dashboard/",
        include("apps.galleries.direct_uploads.urls", namespace="direct_uploads"),
    ),
    path(
        "dashboard/galleries/<uuid:gallery_uuid>/ai/",
        include("apps.mapache_ai.dashboard_urls", namespace="mapache_ai_dashboard"),
    ),
    path("dashboard/ai/", mapache_ai_views.dashboard_ai_entry, name="mapache_ai_entry"),
    path("dashboard/", include("apps.accounts.urls", namespace="accounts")),
    path("", include("apps.galleries.public_urls", namespace="galleries_public")),
    path(
        "g/<slug:slug>/find-me/",
        include("apps.mapache_ai.public_urls", namespace="mapache_ai_public"),
    ),
    path("", include("apps.website.urls", namespace="website")),
]
