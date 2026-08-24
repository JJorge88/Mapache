from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.galleries.models import Gallery
from apps.galleries.selectors import get_public_gallery_photos

from .bib.services import (
    bib_results_cache_key,
    check_bib_search_rate_limit,
    create_bib_search_session,
    delete_gallery_bib_index,
    request_gallery_bib_reindex,
    search_bibs_in_gallery,
)
from .combined.services import (
    check_combined_search_rate_limit,
    combined_results_cache_key,
    complete_combined_search_session,
    create_combined_search_session,
    search_combined_in_gallery,
)
from .engines import get_face_engine
from .exceptions import (
    FaceEngineError,
    FaceEngineUnavailable,
    MultipleFacesDetected,
    NoFaceDetected,
)
from .forms import BibSearchForm, CombinedSearchForm, FaceSearchForm, GalleryAISettingsForm
from .models import (
    BibSearchSession,
    CombinedSearchSession,
    FaceSearchSession,
    GalleryAISettings,
)
from .selectors import (
    get_ai_settings,
    get_ai_status,
    get_bib_search_results,
    get_face_search_results,
)
from .services import (
    check_search_rate_limit,
    complete_search_session,
    configure_gallery_ai,
    create_search_session,
    delete_gallery_face_index,
    request_gallery_reindex,
    results_cache_key,
    run_face_query,
)


def _validation_message(exc: ValidationError) -> str:
    return exc.messages[0]


@login_required
def dashboard_ai_entry(request: HttpRequest) -> HttpResponse:
    gallery = Gallery.objects.order_by("-created_at").first()
    if gallery is None:
        return redirect("galleries_dashboard:list")
    return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)


@login_required
def dashboard_ai(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    ai_settings = get_ai_settings(gallery)
    form_data = request.POST if request.method == "POST" else None
    form = GalleryAISettingsForm(
        form_data,
        initial={
            "enabled": ai_settings.enabled,
            "face_search_enabled": ai_settings.face_search_enabled,
            "bib_search_enabled": ai_settings.bib_search_enabled,
            "bib_format": ai_settings.bib_format,
            "bib_min_length": ai_settings.bib_min_length,
            "bib_max_length": ai_settings.bib_max_length,
        },
    )
    if request.method == "POST" and form.is_valid():
        configure_gallery_ai(
            gallery=gallery,
            enabled=form.cleaned_data["enabled"],
            face_search_enabled=form.cleaned_data["face_search_enabled"],
            bib_search_enabled=form.cleaned_data["bib_search_enabled"],
            bib_format=form.cleaned_data["bib_format"],
            bib_min_length=form.cleaned_data["bib_min_length"],
            bib_max_length=form.cleaned_data["bib_max_length"],
            changed_by=request.user,
        )
        messages.success(request, "Configuración de Mapache AI actualizada.")
        return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)
    return render(
        request,
        "dashboard/mapache_ai/settings.html",
        {
            "gallery": gallery,
            "ai_settings": ai_settings,
            "ai_status": get_ai_status(gallery),
            "form": form,
            "global_ai_enabled": settings.MAPACHE_AI_ENABLED,
        },
    )


@login_required
def dashboard_ai_status(request: HttpRequest, gallery_uuid) -> JsonResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    return JsonResponse(get_ai_status(gallery))


@require_POST
@login_required
def dashboard_ai_reindex(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        request_gallery_reindex(gallery=gallery, requested_by=request.user)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "La reindexación facial fue programada.")
    return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def dashboard_ai_delete_index(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    deleted = delete_gallery_face_index(gallery=gallery, deleted_by=request.user)
    messages.success(request, f"Índice facial eliminado ({deleted} registros).")
    return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def dashboard_ai_reindex_bibs(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    try:
        request_gallery_bib_reindex(gallery=gallery, requested_by=request.user)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "La reindexación de números fue programada.")
    return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)


@require_POST
@login_required
def dashboard_ai_delete_bib_index(request: HttpRequest, gallery_uuid) -> HttpResponse:
    gallery = get_object_or_404(Gallery, uuid=gallery_uuid)
    deleted = delete_gallery_bib_index(gallery=gallery, deleted_by=request.user)
    messages.success(request, f"Índice de números eliminado ({deleted} registros).")
    return redirect("mapache_ai_dashboard:settings", gallery_uuid=gallery.uuid)


def _get_public_ai_gallery(
    request: HttpRequest, slug: str, *, capability: str | None = None
) -> Gallery:
    if not settings.MAPACHE_AI_ENABLED:
        raise Http404
    gallery = get_object_or_404(
        Gallery.objects.select_related("ai_settings"),
        slug=slug,
        status=Gallery.Status.PUBLISHED,
    )
    try:
        ai_settings = gallery.ai_settings
    except GalleryAISettings.DoesNotExist as exc:
        raise Http404 from exc
    capability_enabled = {
        "face": ai_settings.face_search_enabled,
        "bib": ai_settings.bib_search_enabled,
        "combined": ai_settings.face_search_enabled and ai_settings.bib_search_enabled,
        None: ai_settings.face_search_enabled or ai_settings.bib_search_enabled,
    }[capability]
    if not ai_settings.enabled or not capability_enabled:
        raise Http404
    if gallery.visibility == Gallery.Visibility.PRIVATE_PIN and not request.session.get(
        f"gallery_access_{gallery.uuid}", False
    ):
        return redirect("galleries_public:access", slug=gallery.slug)
    return gallery


def find_me(request: HttpRequest, slug: str) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug)
    if isinstance(gallery, HttpResponse):
        return gallery
    ai_settings = gallery.ai_settings
    if request.method == "GET" and not ai_settings.face_search_enabled:
        return redirect("mapache_ai_public:find_me_number", slug=gallery.slug)
    form = FaceSearchForm(request.POST or None, request.FILES or None)
    unavailable = False
    if request.method == "POST" and not ai_settings.face_search_enabled:
        raise Http404
    if request.method == "POST" and form.is_valid():
        if not request.session.session_key:
            request.session.create()
        identifier = f"{request.session.session_key}:{request.META.get('REMOTE_ADDR', '')}"
        if not check_search_rate_limit(identifier):
            form.add_error(None, "Has realizado varias búsquedas. Espera unos minutos.")
            return render(
                request,
                "mapache_ai/find_me.html",
                {
                    "gallery": gallery,
                    "form": form,
                    "unavailable": False,
                    "query_max_mb": settings.MAPACHE_FACE_QUERY_MAX_MB,
                    "face_enabled": ai_settings.face_search_enabled,
                    "bib_enabled": ai_settings.bib_search_enabled,
                    "bib_form": BibSearchForm(),
                    "photos": get_public_gallery_photos(gallery),
                },
                status=429,
            )
        session = create_search_session(gallery=gallery)
        uploaded = form.cleaned_data["query_image"]
        try:
            image_bytes = uploaded.read()
            photo_ids = run_face_query(
                gallery=gallery, image_bytes=image_bytes, engine=get_face_engine()
            )
        except NoFaceDetected:
            session.status = FaceSearchSession.Status.ERROR
            session.save(update_fields=["status"])
            form.add_error(None, "No encontramos un rostro claro. Prueba con otra fotografía.")
        except MultipleFacesDetected:
            session.status = FaceSearchSession.Status.ERROR
            session.save(update_fields=["status"])
            form.add_error(
                None,
                "Vemos varias personas. Utiliza una fotografía donde aparezcas principalmente tú.",
            )
        except (FaceEngineUnavailable, FaceEngineError):
            session.status = FaceSearchSession.Status.ERROR
            session.save(update_fields=["status"])
            unavailable = True
        finally:
            uploaded.close()
        if not form.non_field_errors() and not unavailable:
            complete_search_session(session, photo_ids)
            return redirect(
                "mapache_ai_public:results", slug=gallery.slug, session_uuid=session.uuid
            )
    return render(
        request,
        "mapache_ai/find_me.html",
        {
            "gallery": gallery,
            "form": form,
            "unavailable": unavailable,
            "query_max_mb": settings.MAPACHE_FACE_QUERY_MAX_MB,
            "face_enabled": ai_settings.face_search_enabled,
            "bib_enabled": ai_settings.bib_search_enabled,
            "bib_form": BibSearchForm(),
            "photos": get_public_gallery_photos(gallery),
        },
    )


def find_me_results(request: HttpRequest, slug: str, session_uuid) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug, capability="face")
    if isinstance(gallery, HttpResponse):
        return gallery
    session = get_object_or_404(
        FaceSearchSession,
        uuid=session_uuid,
        gallery_id=gallery.id,
        status=FaceSearchSession.Status.COMPLETED,
    )
    if session.is_expired:
        cache.delete(results_cache_key(session.uuid))
        raise Http404
    photo_ids = cache.get(results_cache_key(session.uuid))
    if photo_ids is None:
        raise Http404
    photos = get_face_search_results(gallery, photo_ids)
    return render(
        request,
        "mapache_ai/results.html",
        {"gallery": gallery, "search_session": session, "photos": photos},
    )


def find_me_number(request: HttpRequest, slug: str) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug, capability="bib")
    if isinstance(gallery, HttpResponse):
        return gallery
    form = BibSearchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not request.session.session_key:
            request.session.create()
        identifier = f"{request.session.session_key}:{request.META.get('REMOTE_ADDR', '')}"
        if not check_bib_search_rate_limit(identifier):
            form.add_error(None, "Has realizado varias búsquedas. Espera unos minutos.")
            return render(
                request,
                "mapache_ai/find_me_number.html",
                {
                    "gallery": gallery,
                    "form": form,
                    "photos": get_public_gallery_photos(gallery),
                    "search_performed": False,
                },
                status=429,
            )
        try:
            normalized, photo_ids = search_bibs_in_gallery(
                gallery=gallery, query_number=form.cleaned_data["query_number"]
            )
        except ValidationError as exc:
            form.add_error("query_number", _validation_message(exc))
        else:
            session = create_bib_search_session(
                gallery=gallery, normalized_number=normalized, photo_ids=photo_ids
            )
            results_url = reverse(
                "mapache_ai_public:bib_results", args=[gallery.slug, session.uuid]
            )
            return redirect(f"{results_url}#fotos")
    return render(
        request,
        "mapache_ai/find_me_number.html",
        {
            "gallery": gallery,
            "form": form,
            "photos": get_public_gallery_photos(gallery),
            "search_performed": False,
        },
    )


def find_me_number_results(request: HttpRequest, slug: str, session_uuid) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug, capability="bib")
    if isinstance(gallery, HttpResponse):
        return gallery
    session = get_object_or_404(BibSearchSession, uuid=session_uuid, gallery_id=gallery.id)
    if session.is_expired:
        cache.delete(bib_results_cache_key(session.uuid))
        raise Http404
    photo_ids = cache.get(bib_results_cache_key(session.uuid))
    if photo_ids is None:
        raise Http404
    photos = get_bib_search_results(gallery, photo_ids)
    return render(
        request,
        "mapache_ai/find_me_number.html",
        {
            "gallery": gallery,
            "form": BibSearchForm(initial={"query_number": session.normalized_number}),
            "search_session": session,
            "search_performed": True,
            "photos": photos,
        },
    )


def find_me_combined(request: HttpRequest, slug: str) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug, capability="combined")
    if isinstance(gallery, HttpResponse):
        return gallery
    form = CombinedSearchForm(request.POST or None, request.FILES or None)
    unavailable = False
    if request.method == "POST" and form.is_valid():
        if not request.session.session_key:
            request.session.create()
        identifier = f"{request.session.session_key}:{request.META.get('REMOTE_ADDR', '')}"
        if not check_combined_search_rate_limit(identifier):
            form.add_error(None, "Has realizado varias búsquedas. Espera unos minutos.")
            return render(
                request,
                "mapache_ai/find_me_combined.html",
                {
                    "gallery": gallery,
                    "form": form,
                    "unavailable": False,
                    "query_max_mb": settings.MAPACHE_FACE_QUERY_MAX_MB,
                },
                status=429,
            )
        uploaded = form.cleaned_data["query_image"]
        try:
            normalized, results, face_count, bib_count = search_combined_in_gallery(
                gallery=gallery,
                image_bytes=uploaded.read(),
                query_number=form.cleaned_data["query_number"],
                face_engine=get_face_engine(),
            )
        except NoFaceDetected:
            form.add_error(None, "No encontramos un rostro claro. Prueba con otra fotografía.")
        except MultipleFacesDetected:
            form.add_error(
                None,
                "Vemos varias personas. Utiliza una fotografía donde aparezcas principalmente tú.",
            )
        except ValidationError as exc:
            form.add_error("query_number", _validation_message(exc))
        except (FaceEngineUnavailable, FaceEngineError):
            unavailable = True
        finally:
            uploaded.close()
        if not form.errors and not unavailable:
            session = create_combined_search_session(gallery=gallery, normalized_number=normalized)
            complete_combined_search_session(
                session,
                results,
                face_results_count=face_count,
                bib_results_count=bib_count,
            )
            return redirect(
                "mapache_ai_public:combined_results",
                slug=gallery.slug,
                session_uuid=session.uuid,
            )
    return render(
        request,
        "mapache_ai/find_me_combined.html",
        {
            "gallery": gallery,
            "form": form,
            "unavailable": unavailable,
            "query_max_mb": settings.MAPACHE_FACE_QUERY_MAX_MB,
        },
    )


def find_me_combined_results(request: HttpRequest, slug: str, session_uuid) -> HttpResponse:
    gallery = _get_public_ai_gallery(request, slug, capability="combined")
    if isinstance(gallery, HttpResponse):
        return gallery
    session = get_object_or_404(
        CombinedSearchSession,
        uuid=session_uuid,
        gallery_id=gallery.id,
        status=CombinedSearchSession.Status.COMPLETED,
    )
    if session.is_expired:
        cache.delete(combined_results_cache_key(session.uuid))
        raise Http404
    photo_ids = cache.get(combined_results_cache_key(session.uuid))
    if photo_ids is None:
        raise Http404
    photos = get_bib_search_results(gallery, photo_ids)
    return render(
        request,
        "mapache_ai/combined_results.html",
        {"gallery": gallery, "search_session": session, "photos": photos},
    )
