from django.urls import path

from .views import contact, home, services, studio

app_name = "website"
urlpatterns = [
    path("", home, name="home"),
    path("services/", services, name="services"),
    path("studio/", studio, name="studio"),
    path("contact/", contact, name="contact"),
]
