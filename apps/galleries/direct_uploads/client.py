from functools import lru_cache

import boto3
from botocore.config import Config
from django.conf import settings


class R2DirectUploadClient:
    def __init__(self, client=None):
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self.bucket = settings.R2_BUCKET_NAME

    def presign_put(self, *, key, content_type, expires):
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
            HttpMethod="PUT",
        )

    def create_multipart(self, *, key, content_type):
        response = self.client.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            ContentType=content_type,
        )
        return response["UploadId"]

    def presign_part(self, *, key, upload_id, part_number, expires):
        return self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )

    def list_parts(self, *, key, upload_id):
        parts = []
        marker = 0
        while True:
            response = self.client.list_parts(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                PartNumberMarker=marker,
            )
            parts.extend(
                {"part_number": part["PartNumber"], "etag": part["ETag"]}
                for part in response.get("Parts", [])
            )
            if not response.get("IsTruncated"):
                return parts
            marker = response["NextPartNumberMarker"]

    def complete_multipart(self, *, key, upload_id, parts):
        return self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": part["part_number"], "ETag": part["etag"]} for part in parts
                ]
            },
        )

    def abort_multipart(self, *, key, upload_id):
        return self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
        )

    def head(self, *, key):
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def delete(self, *, key):
        return self.client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache(maxsize=1)
def get_direct_upload_client():
    return R2DirectUploadClient()
