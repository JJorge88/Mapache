from django.urls import path

from .views import DashboardLoginView, DashboardLogoutView, dashboard

app_name = "accounts"

urlpatterns = [
    path("login/", DashboardLoginView.as_view(), name="login"),
    path("logout/", DashboardLogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
]
