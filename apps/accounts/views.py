from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse_lazy

from apps.galleries.models import Gallery, Photo
from apps.galleries.selectors import get_recent_galleries
from apps.mapache_ai.models import GalleryAISettings

from .forms import DashboardAuthenticationForm


class DashboardLoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = DashboardAuthenticationForm
    redirect_authenticated_user = True


class DashboardLogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("accounts:login")
    http_method_names = ["post"]


@login_required
def dashboard(request):
    counts = {
        "total_galleries": Gallery.objects.count(),
        "total_photos": Photo.objects.count(),
        "published_galleries": Gallery.objects.filter(status=Gallery.Status.PUBLISHED).count(),
        "private_galleries": Gallery.objects.filter(
            visibility=Gallery.Visibility.PRIVATE_PIN
        ).count(),
        "ai_galleries": GalleryAISettings.objects.filter(enabled=True).count(),
    }
    counts["recent_galleries"] = get_recent_galleries()
    return render(request, "dashboard/home.html", counts)
