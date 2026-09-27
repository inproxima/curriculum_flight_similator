"""FastAPI application entry point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import cfs.handlers  # noqa: F401  (register job handlers for eager mode)
from cfs.api.v1 import ai, analysis, catalog, documents, scenarios, system
from cfs.core.config import get_settings
from cfs.core.errors import AppError, app_error_handler

logging.basicConfig(level=logging.INFO)
settings = get_settings()

app = FastAPI(
    title="Curriculum Flight Simulator API",
    version="0.1.0",
    description="Curriculum decision support. Not a predictor of learning gains or an accreditation certifier.",
)
app.add_exception_handler(AppError, app_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)

for r in (system.router, ai.router, catalog.router, documents.router, analysis.router, scenarios.router):
    app.include_router(r, prefix="/api/v1")
