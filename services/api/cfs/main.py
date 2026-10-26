"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

import cfs.handlers  # noqa: F401  (register job handlers for eager mode)
from cfs.api.v1 import admin, ai, alignment, analysis, catalog, documents, outlines, scenarios, system
from cfs.core.config import get_settings
from cfs.core.errors import AppError, app_error_handler
from cfs.core.security import SecurityHeaders

logging.basicConfig(level=logging.INFO)
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.job_runner == "thread" and not settings.task_always_eager:
        from cfs.core import runner

        runner.start()
        try:
            yield
        finally:
            runner.stop()
    else:
        yield


app = FastAPI(
    title="Curriculum Flight Simulator API",
    version="0.1.0",
    description="Curriculum decision support. Not a predictor of learning gains or an accreditation certifier.",
    lifespan=lifespan,
)
app.add_exception_handler(AppError, app_error_handler)
app.add_middleware(SecurityHeaders)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)

for r in (
    system.router,
    admin.router,
    ai.router,
    alignment.router,
    catalog.router,
    documents.router,
    outlines.router,
    analysis.router,
    scenarios.router,
):
    app.include_router(r, prefix="/api/v1")

if settings.web_dist_dir:
    from cfs.web_static import mount_web

    mount_web(app, settings.web_dist_dir)
