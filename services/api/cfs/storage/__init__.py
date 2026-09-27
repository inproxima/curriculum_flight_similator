from functools import lru_cache

from cfs.core.config import get_settings
from cfs.storage.base import ObjectStore, content_key  # noqa: F401


@lru_cache
def get_store() -> ObjectStore:
    s = get_settings()
    if s.storage_backend == "s3":
        from cfs.storage.s3 import S3Store

        if not s.s3_bucket:
            raise RuntimeError("CFS_S3_BUCKET is required when CFS_STORAGE_BACKEND=s3")
        return S3Store(s.s3_bucket, s.s3_region)
    from cfs.storage.local import LocalFSStore

    return LocalFSStore(s.storage_root)
