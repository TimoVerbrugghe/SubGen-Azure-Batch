"""
Routers package for SubGen-Azure-Batch.

Exports all API routers for the FastAPI application.
"""

from app.routers.asr import router as asr_router
from app.routers.batch import router as batch_router
from app.routers.ui import router as ui_router
from app.routers.webhooks import router as webhooks_router

__all__ = [
    "asr_router",
    "batch_router",
    "ui_router",
    "webhooks_router",
]
