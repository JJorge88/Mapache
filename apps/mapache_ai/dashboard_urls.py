from django.urls import path

from . import views

app_name = "mapache_ai_dashboard"

urlpatterns = [
    path("", views.dashboard_ai, name="settings"),
    path("status/", views.dashboard_ai_status, name="status"),
    path("reindex/", views.dashboard_ai_reindex, name="reindex"),
    path("delete-index/", views.dashboard_ai_delete_index, name="delete_index"),
    path("reindex-bibs/", views.dashboard_ai_reindex_bibs, name="reindex_bibs"),
    path("delete-bib-index/", views.dashboard_ai_delete_bib_index, name="delete_bib_index"),
]
