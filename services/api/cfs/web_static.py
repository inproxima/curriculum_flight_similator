"""Serve the built web app (apps/web/dist) from the API, so one service hosts both on one origin.

API routes are registered first and always win. Any other GET returns the file at that path if the build has
one, and index.html otherwise, so client-side routes (/connections, /sequence, …) load on refresh.
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from cfs.core.errors import NotFound

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}  # Vite hashes everything in assets/
REVALIDATE = {"Cache-Control": "no-cache"}


def mount_web(app: FastAPI, dist_dir: str) -> None:
    root = Path(dist_dir).resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"CFS_WEB_DIST_DIR has no index.html: {root}")

    @app.get("/{path:path}", include_in_schema=False)
    def web(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise NotFound("No such API route")
        if path:
            f = (root / path).resolve()
            if f.is_relative_to(root) and f.is_file():
                return FileResponse(f, headers=IMMUTABLE if path.startswith("assets/") else REVALIDATE)
        return FileResponse(index, headers=REVALIDATE)
