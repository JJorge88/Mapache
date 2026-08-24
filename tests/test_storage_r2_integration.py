import os
import time
import urllib.request
import uuid
import zipfile
from io import BytesIO

import pytest
from django.core.files.base import ContentFile

from apps.core.storage_backends import R2MediaStorage
from apps.galleries.direct_uploads.client import R2DirectUploadClient
from apps.galleries.direct_uploads.services import confirm_upload, initialize_uploads
from apps.galleries.services import delete_photo
from apps.media_processing.services import process_photo_image
from tests.factories import make_gallery, make_user
from tests.image_helpers import make_test_image

pytestmark = pytest.mark.integration

R2_REQUIRED = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)


@pytest.mark.skipif(
    os.environ.get("MAPACHE_RUN_R2_INTEGRATION") != "1"
    or any(not os.environ.get(name) for name in R2_REQUIRED),
    reason="R2 smoke requires opt-in and credentials from environment.",
)
def test_r2_write_read_delete_smoke():
    endpoint = os.environ.get("R2_ENDPOINT_URL") or (
        f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    )
    storage = R2MediaStorage(
        bucket_name=os.environ["R2_BUCKET_NAME"],
        access_key=os.environ["R2_ACCESS_KEY_ID"],
        secret_key=os.environ["R2_SECRET_ACCESS_KEY"],
        endpoint_url=endpoint,
        region_name="auto",
    )
    name = f"_mapache_r2_smoke/{uuid.uuid4}.txt"
    payload = b"mapache-r2-smoke"
    timings = {}
    try:
        started = time.perf_counter()
        storage.save(name, ContentFile(payload))
        timings["write_ms"] = round((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        with storage.open(name, "rb") as stored:
            assert stored.read() == payload
        timings["read_ms"] = round((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        storage.delete(name)
        timings["delete_ms"] = round((time.perf_counter() - started) * 1000)
        assert not storage.exists(name)
        print(timings)
    finally:
        storage.delete(name)


@pytest.mark.skipif(
    os.environ.get("MAPACHE_RUN_R2_INTEGRATION") != "1"
    or any(not os.environ.get(name) for name in R2_REQUIRED),
    reason="R2 ZIP smoke requires opt-in and credentials from environment.",
)
def test_r2_zip_write_read_delete_smoke():
    endpoint = os.environ.get("R2_ENDPOINT_URL") or (
        f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    )
    storage = R2MediaStorage(
        bucket_name=os.environ["R2_BUCKET_NAME"],
        access_key=os.environ["R2_ACCESS_KEY_ID"],
        secret_key=os.environ["R2_SECRET_ACCESS_KEY"],
        endpoint_url=endpoint,
        region_name="auto",
    )
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zipped:
        zipped.writestr("001_smoke.txt", b"mapache-r2-zip-smoke")
    payload = archive.getvalue()
    name = f"_mapache_r2_smoke/{uuid.uuid4}.zip"
    try:
        storage.save(name, ContentFile(payload))
        with storage.open(name, "rb") as stored:
            downloaded = stored.read()
        with zipfile.ZipFile(BytesIO(downloaded)) as zipped:
            assert zipped.read("001_smoke.txt") == b"mapache-r2-zip-smoke"
        storage.delete(name)
        assert not storage.exists(name)
    finally:
        storage.delete(name)


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    os.environ.get("MAPACHE_RUN_R2_INTEGRATION") != "1"
    or any(not os.environ.get(name) for name in R2_REQUIRED),
    reason="R2 direct upload smoke requires opt-in and credentials from environment.",
)
def test_r2_direct_upload_confirm_process_cleanup_smoke(settings):
    endpoint = os.environ.get("R2_ENDPOINT_URL") or (
        f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com"
    )
    settings.STORAGE_BACKEND = "r2"
    settings.MAPACHE_DIRECT_UPLOAD_ENABLED = True
    settings.R2_ACCOUNT_ID = os.environ["R2_ACCOUNT_ID"]
    settings.R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
    settings.R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
    settings.R2_BUCKET_NAME = os.environ["R2_BUCKET_NAME"]
    settings.R2_ENDPOINT_URL = endpoint
    settings.STORAGES = {
        "default": {
            "BACKEND": "apps.core.storage_backends.R2MediaStorage",
            "OPTIONS": {
                "bucket_name": settings.R2_BUCKET_NAME,
                "access_key": settings.R2_ACCESS_KEY_ID,
                "secret_key": settings.R2_SECRET_ACCESS_KEY,
                "endpoint_url": endpoint,
                "region_name": "auto",
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    image = make_test_image("generated-smoke.jpg", size=(64, 40))
    payload = image.read()
    user = make_user(f"r2-smoke-{uuid.uuid4()}")
    gallery = make_gallery(user, title=f"R2 smoke {uuid.uuid4()}")
    client = R2DirectUploadClient()
    photo = None
    object_key = ""
    timings = {}
    try:
        started = time.perf_counter()
        batch, initialized = initialize_uploads(
            gallery=gallery,
            user=user,
            metadata=[
                {
                    "name": "generated-smoke.jpg",
                    "size": len(payload),
                    "type": "image/jpeg",
                    "last_modified": 1,
                }
            ],
            client=client,
        )
        timings["presign_ms"] = round((time.perf_counter() - started) * 1000)
        item = batch.items.get()
        object_key = item.object_key
        request = urllib.request.Request(
            initialized[0]["upload_url"],
            data=payload,
            method="PUT",
            headers={"Content-Type": "image/jpeg"},
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=30) as response:
            assert 200 <= response.status < 300
        timings["upload_ms"] = round((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        item = confirm_upload(item=item, user=user, client=client)
        timings["confirm_ms"] = round((time.perf_counter() - started) * 1000)
        photo = process_photo_image(item.photo_id)
        assert photo.processing_status == photo.ProcessingStatus.READY
        assert photo.original_file.storage.exists(photo.original_file.name)
        assert photo.optimized_file.storage.exists(photo.optimized_file.name)
        print(timings)
    finally:
        if photo:
            delete_photo(photo=photo, deleted_by=user)
        elif object_key:
            client.delete(key=object_key)
