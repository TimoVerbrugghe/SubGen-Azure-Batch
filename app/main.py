"""
SubGen-Azure-Batch - Cloud-based subtitle generation using Azure Batch Transcription API.

This is the main FastAPI application entry point.
"""

import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.agent_api import get_configured_agent_api_key
from app.config import SUBGEN_AZURE_BATCH_VERSION, get_settings
from app.mcp_server import mcp_server
from app.routers import (
    agent_router,
    asr_router,
    batch_router,
    ui_router,
    webhooks_router,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Suppress verbose Azure SDK HTTP logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
    logging.WARNING
)


# Reduce noise from uvicorn access logs for status polling
class SuppressStatusPollingFilter(logging.Filter):
    """Filter out noisy status polling requests from access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # Suppress session status polling and health checks
        if "/api/batch/session/" in message and "GET" in message:
            return False
        if "/health" in message:
            return False
        return True


# Apply filter to uvicorn access logger
logging.getLogger("uvicorn.access").addFilter(SuppressStatusPollingFilter())


class MCPAuthenticationMiddleware:
    """Authenticate MCP without buffering or completing downstream ASR streams."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass ASGI messages through unchanged except for rejected MCP requests."""
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/mcp" or path.startswith("/mcp/")):
            configured_key = get_configured_agent_api_key()
            if not configured_key:
                response = JSONResponse(
                    status_code=503,
                    content={
                        "detail": (
                            "Agent API is disabled until AGENT_API_KEY is configured "
                            "with at least 32 non-whitespace characters."
                        )
                    },
                )
                await response(scope, receive, send)
                return
            supplied_key = Headers(scope=scope).get("X-API-Key", "")
            if not hmac.compare_digest(supplied_key, configured_key):
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key."},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


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
        logger.warning(
            "Azure Speech Services not configured! Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
        )

    async with mcp_server.session_manager.run():
        yield

    # Shutdown
    logger.info("SubGen-Azure-Batch Shutting Down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    get_settings()

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

    app.add_middleware(MCPAuthenticationMiddleware)

    # Mount static files if directory exists
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Include routers
    app.include_router(ui_router)  # Root routes (/, /health, /api/config, etc.)
    app.include_router(asr_router)  # /asr endpoint for Bazarr
    app.include_router(webhooks_router)  # /webhook/* endpoints
    app.include_router(batch_router)  # /api/batch/* endpoints
    app.include_router(agent_router)  # Authenticated agent batch endpoints
    app.mount("/mcp", mcp_server.streamable_http_app(), name="mcp")

    return app


# Create the application instance
app = create_app()


def main():
    """Run the application with uvicorn."""
    settings = get_settings()

    # Build uvicorn config
    uvicorn_kwargs = {
        "host": "0.0.0.0",
        "port": 9000,  # Fixed for Docker deployments, change in docker-compose.yml if needed
        "reload": settings.debug,
        "log_level": "debug" if settings.debug else "info",
    }

    # HTTP idle timeout between requests; does not protect active transcriptions.
    # Only set if explicitly configured via UVICORN_TIMEOUT_KEEP_ALIVE environment variable
    timeout_keep_alive_env = os.getenv("UVICORN_TIMEOUT_KEEP_ALIVE")
    if timeout_keep_alive_env is not None:
        uvicorn_kwargs["timeout_keep_alive"] = int(timeout_keep_alive_env)

    uvicorn.run("app.main:app", **uvicorn_kwargs)


if __name__ == "__main__":
    main()
