"""PostgreSQL-backed object store for small single-service deployments (no persistent disk or bucket).

Originals live in the stored_objects table next to their metadata, so a database backup also covers the files.
"""

import io
from typing import BinaryIO

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from cfs.core.db import get_sessionmaker
from cfs.models import StoredObject


class DbStore:
    def put(self, key: str, data: bytes, content_type: str) -> None:
        stmt = insert(StoredObject).values(key=key, content_type=content_type, size=len(data), data=data)
        stmt = stmt.on_conflict_do_update(
            index_elements=[StoredObject.key],
            set_={"content_type": content_type, "size": len(data), "data": data},
        )
        with get_sessionmaker()() as db:
            db.execute(stmt)
            db.commit()

    def get(self, key: str) -> bytes:
        with get_sessionmaker()() as db:
            data = db.scalar(select(StoredObject.data).where(StoredObject.key == key))
        if data is None:
            raise FileNotFoundError(key)
        return bytes(data)

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        with get_sessionmaker()() as db:
            return db.scalar(select(StoredObject.key).where(StoredObject.key == key)) is not None

    def delete(self, key: str) -> None:
        with get_sessionmaker()() as db:
            db.execute(delete(StoredObject).where(StoredObject.key == key))
            db.commit()

    def healthcheck(self) -> None:
        with get_sessionmaker()() as db:
            db.execute(text("select 1 from stored_objects limit 1"))
