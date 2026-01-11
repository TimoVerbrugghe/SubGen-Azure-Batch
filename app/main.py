"""
SubGen-Azure-Batch - Cloud-based subtitle generation using Azure Batch Transcription API.

This is the main FastAPI application entry point.
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import SUBGEN_AZURE_BATCH_VERSION, get_settings
from app.routers import asr_router, batch_router, ui_router, webhooks_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Suppress verbose Azure SDK HTTP logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

# Reduce noise from uvicorn access logs for status polling
class SuppressStatusPollingFilter(logging.Filter):
    """Filter out noisy status polling requests from access logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # Suppress session status polling and health checks
        if '/api/batch/session/' in message and 'GET' in message:
            return False
        if '/health' in message:
            return False
        return True

# Apply filter to uvicorn access logger
logging.getLogger("uvicorn.access").addFilter(SuppressStatusPollingFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Startup
    settings = get_settings()
    logger.info("=" * 60)
    logger.info("SubGen-Azure-Batch Starting Up")
    logger.info("=" * 60)
    logger.info(f"Azure Speech Region: {settings.azure.speech_region}")
    logger.info(f"Azure Configured: {settings.azure.is_configured}")
    logger.info(f"Bazarr Configured: {settings.bazarr.is_configured}")
    logger.info(f"Plex Configured: {settings.plex.is_configured}")
    logger.info(f"Jellyfin Configured: {settings.jellyfin.is_configured}")
    logger.info(f"Emby Configured: {settings.emby.is_configured}")
    logger.info(f"Media Folders: {settings.media_folders}")
    logger.info(f"Concurrent Transcriptions: {settings.concurrent_transcriptions}")
    logger.info("=" * 60)
    
    if not settings.azure.is_configured:
        logger.warning("Azure Speech Services not configured! Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.")
    
    yield
    
    # Shutdown
    logger.info("SubGen-Azure-Batch Shutting Down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title="SubGen-Azure-Batch",
        description="Cloud-based subtitle generation using Azure Batch Transcription API",
        version=SUBGEN_AZURE_BATCH_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure as needed for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Mount static files if directory exists
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Include routers
    app.include_router(ui_router)  # Root routes (/, /health, /api/config, etc.)
    app.include_router(asr_router)  # /asr endpoint for Bazarr
    app.include_router(webhooks_router)  # /webhook/* endpoints
    app.include_router(batch_router)  # /api/batch/* endpoints
    
    return app


# Create the application instance
app = create_app()


def main():
    """Run the application with uvicorn."""
    settings = get_settings()
    
    # Port is fixed at 9000 for Docker deployments
    # Change the port in docker-compose.yml or docker run command if needed
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=9000,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )


if __name__ == "__main__":
    main()
