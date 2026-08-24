from django.conf import settings
from storages.backends.s3 import S3Storage


class R2MediaStorage(S3Storage):
    """Private R2 storage. Delivery policy lives outside the persistence backend."""

    default_acl = None
    file_overwrite = True
    querystring_auth = True
    signature_version = "s3v4"
    addressing_style = "path"

    def get_object_parameters(self, name):
        parameters = super().get_object_parameters(name)
        parameters["ContentDisposition"] = "inline"
        if "/originals/" in name:
            parameters["CacheControl"] = "private, no-store"
        else:
            parameters["CacheControl"] = (
                f"private, max-age={settings.MAPACHE_PRIVATE_MEDIA_URL_TTL}"
            )
        return parameters
