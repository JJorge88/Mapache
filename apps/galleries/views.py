import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.media_delivery import get_optimized_url, get_thumbnail_url

from .forms import (
    GalleryAccessForm,
    GalleryCreateForm,
    GalleryEditForm,
    GalleryPinAccessForm,
)
from .models import Gallery, GalleryUploadBatch
from .selectors import (
    get_dashboard_galleries,
    get_event_galleries,
    get_gallery_by_slug,
    get_gallery_photo_order,
    get_gallery_photo_page,
    get_gallery_photos,
    get_gallery_processing_stats,
    get_public_gallery_photos,
)
from .services import (
    archive_gallery,
    change_gallery_pin,
    change_gallery_visibility,
    create_gallery,
    publish_gallery,
    reorder_photos,
    set_gallery_cover,
    update_gallery,
)
from .sharing import generate_gallery_qr, get_public_gallery_url

MAX_PIN_ATTEMPTS = 5
PIN_LOCK_MINUTES = 5


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return next(iter(exc.message_dict.values()))[0]
    return exc.messages[0]


@login_required
def gallery_list(request: HttpRequest) -> HttpResponse:
    galleries = get_dashboard_galleries()
    selected_filter = request.GET.get("status", "all").lower()
    filters = {
        "published": Q(status=Gallery.Status.PUBLISHED),
        "private": Q(visibility=Gallery.Visibility.PRIVATE_PIN),
        "draft": Q(status=Gallery.Status.DRAFT),
        "archived": Q(status=Gallery.Status.ARCHIVED),
    }
    if selected_filter in filters:
        galleries = galleries.filter(filters[selected_filter])
    else:
        selected_filter = "all"
    query = request.GET.get("q", "").strip()
    if query:
        galleries = galleries.filter(Q(title__icontains=query) | Q(slug__icontains=query))
    return render(
        request,
        "dashboard/galleries/list.html",
        {"galleries": galleries, "selected_filter": selected_filter, "query": query},
    )


@login_required
def gallery_create(request: HttpRequest) -> HttpResponse:
    form = GalleryCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = {
            key: value
            for key, value in form.cleaned_data.items()
            if key not in {"pin", "enable_mapache_ai"}
        }
        try:
            gallery = create_gallery(created_by=request.user, **data)
            if form.cleaned_data["pin"]:
                change_gallery_pin(
                    gallery=gallery,
                    pin=form.cleaned_data["pin"],
                    changed_by=request.user,
                )
            if form.cleaned_data["enable_mapache_ai"]:
                from apps.mapache_ai.models import GalleryAISettings
                from apps.mapache_ai.services import configure_gallery_ai

                configure_gallery_ai(
                    gallery=gallery,
                    enabled=True,
                    face_search_enabled=True,
                    bib_search_enabled=True,
                    bib_format=GalleryAISettings.BibFormat.NUMERIC,
                    bib_min_length=1,
                    bib_max_length=6,
                    changed_by=request.user,
                )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Galería creada correctamente.")
            return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)
    return render(request, "dashboard/galleries/form.html", {"form": form, "mode": "create"})


@login_required
def gallery_detail(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(
        get_dashboard_galleries(),
        uuid=gallery_uuid,
    )
    return render(
        request,
        "dashboard/galleries/detail.html",
        {
            "gallery": gallery,
            "photos": get_gallery_photos(gallery),
            "processing_stats": get_gallery_processing_stats(gallery),
            "public_url": get_public_gallery_url(gallery),
            "published_success": request.GET.get("published") == "1",
        },
    )


@login_required
def gallery_photos(request: HttpRequest, gallery_uuid) -> HttpResponse:
    from apps.media_processing.forms import MultiplePhotoUploadForm

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    batch_id = request.GET.get("batch_id", "")[:64]
    summary_key = (
        f"upload_summary_{gallery.uuid}_{batch_id}"
        if batch_id
        else f"upload_summary_{gallery.uuid}"
    )
    page = get_gallery_photo_page(gallery, page_number=request.GET.get("page", 1))
    direct_upload_enabled = bool(
        settings.MAPACHE_DIRECT_UPLOAD_ENABLED and settings.STORAGE_BACKEND == "r2"
    )
    active_upload_batch = None
    if direct_upload_enabled:
        active_upload_batch = (
            gallery.upload_batches.filter(
                created_by=request.user,
                status__in=[
                    GalleryUploadBatch.Status.CREATED,
                    GalleryUploadBatch.Status.UPLOADING,
                    GalleryUploadBatch.Status.PROCESSING,
                    GalleryUploadBatch.Status.PARTIAL,
                ],
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )
    return render(
        request,
        "dashboard/galleries/photos.html",
        {
            "gallery": gallery,
            "photos": page.object_list,
            "photo_page": page,
            "photo_order": get_gallery_photo_order(gallery),
            "processing_stats": get_gallery_processing_stats(gallery),
            "upload_form": MultiplePhotoUploadForm(),
            "upload_summary": request.session.pop(summary_key, None),
            "max_photo_size_mb": settings.MAPACHE_MAX_PHOTO_SIZE_MB,
            "upload_batch_size": settings.MAPACHE_UPLOAD_BATCH_SIZE,
            "direct_upload_enabled": direct_upload_enabled,
            "direct_upload_concurrency": settings.MAPACHE_UPLOAD_CONCURRENCY,
            "active_upload_batch": active_upload_batch,
        },
    )


@require_POST
@login_required
def gallery_photos_upload(request: HttpRequest, gallery_uuid) -> HttpResponse:
    from apps.media_processing.forms import MultiplePhotoUploadForm
    from apps.media_processing.services import upload_photo

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    form = MultiplePhotoUploadForm(request.POST, request.FILES)
    accepted = 0
    errors = []
    received = len(request.FILES.getlist("photos"))
    if form.is_valid():
        for uploaded_file in form.cleaned_data["photos"]:
            try:
                upload_photo(
                    gallery=gallery,
                    uploaded_file=uploaded_file,
                    uploaded_by=request.user,
                )
            except ValidationError as exc:
                errors.append(
                    {
                        "filename": Path(uploaded_file.name).name,
                        "error": _validation_message(exc),
                    }
                )
            else:
                accepted += 1
    else:
        errors.append({"filename": "Selección", "error": "Selecciona archivos válidos."})
    summary = {
        "received": received,
        "accepted": accepted,
        "rejected": received - accepted,
        "errors": errors,
    }
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax:
        batch_id = request.POST.get("batch_id", "")[:64]
        summary_key = f"upload_summary_{gallery.uuid}_{batch_id}"
        aggregate = request.session.get(
            summary_key,
            {"received": 0, "accepted": 0, "rejected": 0, "errors": []},
        )
        for key in ("received", "accepted", "rejected"):
            aggregate[key] += summary[key]
        aggregate["errors"].extend(summary["errors"])
        request.session[summary_key] = aggregate
        redirect_url = reverse("galleries_dashboard:photos", args=[gallery.uuid])
        return JsonResponse(
            {
                **summary,
                "redirect_url": f"{redirect_url}?batch_id={batch_id}",
            }
        )
    request.session[f"upload_summary_{gallery.uuid}"] = summary
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@login_required
def gallery_photos_status(request: HttpRequest, gallery_uuid) -> JsonResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    photos = get_gallery_photo_page(
        gallery,
        page_number=request.GET.get("page", 1),
    ).object_list
    payload = get_gallery_processing_stats(gallery)
    payload["photos"] = [
        {
            "uuid": str(photo.uuid),
            "status": photo.processing_status,
            "label": photo.get_processing_status_display(),
            "thumbnail_url": (
                get_thumbnail_url(photo=photo, request=request, audience="dashboard")
                if photo.processing_status == photo.ProcessingStatus.READY and photo.thumbnail_file
                else None
            ),
            "preview_url": (
                get_optimized_url(photo=photo, request=request, audience="dashboard")
                if photo.processing_status == photo.ProcessingStatus.READY and photo.optimized_file
                else None
            ),
        }
        for photo in photos
    ]
    return JsonResponse(payload)


@require_POST
@login_required
def gallery_photo_retry(request: HttpRequest, gallery_uuid, photo_uuid) -> HttpResponse:
    from apps.media_processing.services import reprocess_photo

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    photo = get_object_or_404(gallery.photos, uuid=photo_uuid)
    if photo.processing_status == photo.ProcessingStatus.ERROR:
        reprocess_photo(photo=photo, requested_by=request.user)
        messages.success(request, f"Se programó nuevamente {photo.original_filename}.")
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_photo_delete(request: HttpRequest, gallery_uuid, photo_uuid) -> HttpResponse:
    from .services import delete_photo

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    photo = get_object_or_404(gallery.photos, uuid=photo_uuid)
    original_filename = photo.original_filename
    delete_photo(photo=photo, deleted_by=request.user)
    messages.success(request, f"Se eliminó {original_filename}.")
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_photos_bulk_delete(request: HttpRequest, gallery_uuid) -> HttpResponse:
    from .services import delete_photos

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        count = delete_photos(
            gallery=gallery,
            photo_uuids=request.POST.getlist("photo_uuids"),
            deleted_by=request.user,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, f"{count} fotografías eliminadas.")
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_photos_reorder(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        if request.content_type == "application/json":
            photo_uuids = json.loads(request.body).get("photo_uuids", [])
        else:
            photo_uuids = json.loads(request.POST.get("photo_order", "[]"))
        reorder_photos(
            gallery=gallery,
            photo_uuids=photo_uuids,
            reordered_by=request.user,
        )
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "El orden enviado no es válido."}, status=400)
    except ValidationError as exc:
        return JsonResponse({"error": _validation_message(exc)}, status=409)
    messages.success(request, "Orden actualizado.")
    if request.content_type == "application/json":
        return JsonResponse({"status": "ok"})
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_photo_set_cover(request: HttpRequest, gallery_uuid, photo_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    photo = get_object_or_404(gallery.photos, uuid=photo_uuid)
    try:
        set_gallery_cover(gallery=gallery, photo=photo, changed_by=request.user)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Foto establecida como portada.")
    return redirect("galleries_dashboard:photos", gallery_uuid=gallery.uuid)


@login_required
def gallery_share(request: HttpRequest, gallery_uuid) -> JsonResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    return JsonResponse(
        {
            "url": get_public_gallery_url(gallery),
            "visibility": gallery.get_visibility_display(),
            "pin_configured": gallery.has_pin,
        }
    )


@login_required
def gallery_qr(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    png, _public_url = generate_gallery_qr(gallery)
    response = HttpResponse(png, content_type="image/png")
    response["Content-Disposition"] = f'inline; filename="{gallery.slug}-qr.png"'
    response["Cache-Control"] = "private, max-age=300"
    return response


@login_required
def gallery_edit(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    form = GalleryEditForm(request.POST or None, instance=gallery)
    if request.method == "POST" and form.is_valid():
        try:
            update_gallery(
                gallery=gallery,
                updated_by=request.user,
                **form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Galería actualizada.")
            return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)
    return render(
        request,
        "dashboard/galleries/form.html",
        {"form": form, "mode": "edit", "gallery": gallery},
    )


@login_required
def gallery_access_settings(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    form = GalleryAccessForm(request.POST or None, instance=gallery)
    if request.method == "POST" and form.is_valid():
        try:
            pin = form.cleaned_data["pin"]
            if pin:
                change_gallery_pin(gallery=gallery, pin=pin, changed_by=request.user)
            change_gallery_visibility(
                gallery=gallery,
                visibility=form.cleaned_data["visibility"],
                changed_by=request.user,
            )
            update_gallery(
                gallery=gallery,
                updated_by=request.user,
                allow_photo_download=form.cleaned_data["allow_photo_download"],
                allow_gallery_download=form.cleaned_data["allow_gallery_download"],
            )
        except ValidationError as exc:
            form.add_error(None, _validation_message(exc))
        else:
            messages.success(request, "Configuración de acceso actualizada.")
            return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)
    return render(
        request,
        "dashboard/galleries/access.html",
        {"form": form, "gallery": gallery},
    )


@require_POST
@login_required
def gallery_publish(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        publish_gallery(gallery=gallery, published_by=request.user)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Galería publicada.")
        detail_url = reverse("galleries_dashboard:detail", args=[gallery.uuid])
        return redirect(f"{detail_url}?published=1")
    return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_archive(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    archive_gallery(gallery=gallery, archived_by=request.user)
    messages.success(request, "Galería archivada.")
    return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def gallery_downloads_invalidate(request: HttpRequest, gallery_uuid) -> HttpResponse:
    from .downloads import invalidate_gallery_downloads

    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    count = invalidate_gallery_downloads(gallery=gallery, invalidated_by=request.user)
    messages.success(request, f"Se invalidaron {count} descargas preparadas.")
    return redirect("galleries_dashboard:detail", gallery_uuid=gallery.uuid)


def portfolio(request: HttpRequest) -> HttpResponse:
    galleries = get_event_galleries()
    query = request.GET.get("q", "").strip()
    if query:
        galleries = galleries.filter(Q(title__icontains=query) | Q(slug__icontains=query))
    return render(
        request,
        "galleries/portfolio.html",
        {"galleries": galleries, "query": query},
    )


def public_gallery(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        gallery = get_gallery_by_slug(slug)
    except Gallery.DoesNotExist as exc:
        raise Http404 from exc
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN and not request.session.get(
        f"gallery_access_{gallery.uuid}", False
    ):
        return redirect("galleries_public:access", slug=gallery.slug)
    from apps.mapache_ai.models import GalleryAISettings

    ai_settings = GalleryAISettings.objects.filter(gallery=gallery, enabled=True).first()
    ai_search_available = bool(
        settings.MAPACHE_AI_ENABLED
        and ai_settings
        and (ai_settings.face_search_enabled or ai_settings.bib_search_enabled)
    )
    photos = get_public_gallery_photos(gallery)
    return render(
        request,
        "galleries/detail.html",
        {
            "gallery": gallery,
            "photos": photos,
            "photo_count": photos.count(),
            "ai_search_available": ai_search_available,
            "ai_settings": ai_settings,
        },
    )


def gallery_pin_access(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        gallery = get_gallery_by_slug(slug)
    except Gallery.DoesNotExist as exc:
        raise Http404 from exc
    if gallery.visibility != Gallery.Visibility.PRIVATE_PIN:
        return redirect("galleries_public:detail", slug=gallery.slug)
    access_key = f"gallery_access_{gallery.uuid}"
    rate_key = f"gallery_pin_rate_{gallery.uuid}"
    rate = request.session.get(rate_key, {})
    now = timezone.now()
    blocked_until = rate.get("blocked_until")
    is_blocked = bool(blocked_until and now.timestamp() < blocked_until)
    form = GalleryPinAccessForm(request.POST or None)
    if request.method == "POST":
        if is_blocked:
            form.add_error(None, "Demasiados intentos. Intenta nuevamente en unos minutos.")
        elif form.is_valid():
            if gallery.check_pin(form.cleaned_data["pin"]):
                request.session[access_key] = True
                request.session.pop(rate_key, None)
                return redirect("galleries_public:detail", slug=gallery.slug)
            attempts = int(rate.get("attempts", 0)) + 1
            rate = {"attempts": attempts}
            if attempts >= MAX_PIN_ATTEMPTS:
                rate["blocked_until"] = (now + timedelta(minutes=PIN_LOCK_MINUTES)).timestamp()
            request.session[rate_key] = rate
            form.add_error(None, "No fue posible validar el acceso.")
    return render(
        request,
        "galleries/access.html",
        {"gallery": gallery, "form": form, "is_blocked": is_blocked},
    )
