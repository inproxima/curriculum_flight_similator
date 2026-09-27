import io
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError


class S3Store:
    """Private-bucket S3 implementation. Server-side encryption is requested on every write."""

    def __init__(self, bucket: str, region: str | None = None, client=None):
        self.bucket = bucket
        self.client = client or boto3.client("s3", region_name=region)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256"
        )

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def healthcheck(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)
