"""Object-storage interface: local filesystem and S3 (moto; real AWS is not exercised)."""

import boto3
from moto import mock_aws

from cfs.storage.base import content_key
from cfs.storage.local import LocalFSStore
from cfs.storage.s3 import S3Store


def _roundtrip(store):
    key = content_key("org", "ab" * 32, "pdf")
    assert not store.exists(key)
    store.put(key, b"%PDF-1.7 test", "application/pdf")
    assert store.exists(key) and store.get(key) == b"%PDF-1.7 test"
    store.delete(key)
    assert not store.exists(key)
    store.healthcheck()


def test_local_store(tmp_path):
    _roundtrip(LocalFSStore(str(tmp_path)))


@mock_aws
def test_s3_store_same_interface():
    boto3.client("s3", region_name="ca-central-1").create_bucket(
        Bucket="cfs-test", CreateBucketConfiguration={"LocationConstraint": "ca-central-1"}
    )
    _roundtrip(S3Store("cfs-test", "ca-central-1"))


def test_content_key_has_no_user_path_components():
    k = content_key("org", "cd" * 32, "../../pdf")
    assert ".." not in k and k.endswith(".pdf")
