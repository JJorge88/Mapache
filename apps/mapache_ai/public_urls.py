from django.urls import path

from . import views

app_name = "mapache_ai_public"

urlpatterns = [
    path("", views.find_me, name="find_me"),
    path("number/", views.find_me_number, name="find_me_number"),
    path("number/<uuid:session_uuid>/", views.find_me_number_results, name="bib_results"),
    path("combined/", views.find_me_combined, name="find_me_combined"),
    path(
        "combined/<uuid:session_uuid>/",
        views.find_me_combined_results,
        name="combined_results",
    ),
    path("<uuid:session_uuid>/", views.find_me_results, name="results"),
]
