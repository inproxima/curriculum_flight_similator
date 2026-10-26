"""Copy original files from a local object store (data/objects) into PostgreSQL (CFS_STORAGE_BACKEND=db).

Used when moving a local workspace to a single-service host such as Render. It copies every file that a document
version refers to, skips ones already present, and reports any version whose file is missing locally.

    cd services/api
    CFS_DATABASE_URL=<target database URL> uv run python ../../scripts/copy_objects_to_db.py ../../data/objects
"""

import sys
from pathlib import Path

from sqlalchemy import select

from cfs.core.db import get_sessionmaker
from cfs.models import DocumentVersion
from cfs.storage.db import DbStore


def main(root: str) -> int:
    base = Path(root).resolve()
    store = DbStore()
    with get_sessionmaker()() as db:
        versions = db.execute(select(DocumentVersion.object_key, DocumentVersion.mime_type)).all()
    copied = skipped = 0
    missing: list[str] = []
    for key, mime in sorted(set(versions)):
        if store.exists(key):
            skipped += 1
            continue
        path = (base / key).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            missing.append(key)
            continue
        store.put(key, path.read_bytes(), mime or "application/octet-stream")
        copied += 1
    print(f"{len(set(versions))} referenced files: {copied} copied, {skipped} already there, {len(missing)} missing")
    for key in missing:
        print(f"  missing locally: {key}")
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
