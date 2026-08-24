from django.urls import path

from . import views

app_name = "direct_uploads"

urlpatterns = [
    path("uploads/<uuid:item_uuid>/parts/", views.upload_parts, name="parts"),
    path("uploads/<uuid:item_uuid>/complete/", views.upload_complete, name="complete"),
    path("uploads/<uuid:item_uuid>/abort/", views.upload_abort, name="abort"),
    path("upload-batches/<uuid:batch_uuid>/", views.upload_resume, name="resume"),
]
