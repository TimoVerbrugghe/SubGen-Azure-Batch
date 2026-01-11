"""
UI Router - Web interface routes.

Provides routes for:
- Main web interface
- File browser API
- Configuration display
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import SUBGEN_AZURE_BATCH_VERSION, get_settings
from app.language_code import LanguageCode

# Optional Azure imports (may not be installed in all environments)
try:
    from azure.storage.blob import \
        BlobServiceClient  # type: ignore[import-untyped]
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    BlobServiceClient = None  # type: ignore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["UI"])

# Templates directory (will be created in Phase 4)
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


class FileInfo(BaseModel):
    """File information for file browser."""
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    has_subtitle: bool = False


class DirectoryListing(BaseModel):
    """Directory listing response."""
    path: str
    parent: Optional[str]
    items: List[FileInfo]


class ConfigInfo(BaseModel):
    """Configuration information (safe to expose)."""
    azure_configured: bool
    azure_region: str
    bazarr_configured: bool
    plex_configured: bool
    jellyfin_configured: bool
    emby_configured: bool
    media_folders: List[str]
    subtitle_language: str
    concurrent_transcriptions: int
    default_theme: str = "dark"


class ServiceStatus(BaseModel):
    """Status of a single service."""
    configured: bool
    connected: bool
    error: Optional[str] = None


class StatusResponse(BaseModel):
    """Combined status response for all Azure services."""
    speech: ServiceStatus
    storage: ServiceStatus


# Import media extensions from audio_extractor for consistency
from app.audio_extractor import (AUDIO_EXTENSIONS, MEDIA_EXTENSIONS,
                                 VIDEO_EXTENSIONS)
from app.subtitle_utils import SUBTITLE_EXTENSIONS


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    if not TEMPLATES_DIR.exists():
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    return Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main web interface."""
    templates = get_templates()
    settings = get_settings()
    
    # Check if template exists
    index_template = TEMPLATES_DIR / "index.html"
    if not index_template.exists():
        # Return a simple placeholder if template doesn't exist yet
        return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
    <title>SubGen-Azure-Batch</title>
    <style>
        body { font-family: system-ui, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #333; }
        .status { padding: 10px; background: #f0f0f0; border-radius: 4px; margin: 10px 0; }
        .ok { background: #d4edda; color: #155724; }
        .error { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body>
    <h1>SubGen-Azure-Batch</h1>
    <p>Cloud-based subtitle generation using Azure Batch Transcription API.</p>
    
    <h2>Status</h2>
    <div class="status """ + ("ok" if settings.azure.is_configured else "error") + """">
        Azure Speech Services: """ + ("Configured ✓" if settings.azure.is_configured else "Not configured ✗") + """
    </div>
    <div class="status """ + ("ok" if settings.bazarr.is_configured else "") + """">
        Bazarr Integration: """ + ("Configured ✓" if settings.bazarr.is_configured else "Not configured") + """
    </div>
    
    <h2>API Endpoints</h2>
    <ul>
        <li><code>POST /asr</code> - Bazarr-compatible transcription endpoint</li>
        <li><code>POST /webhook/plex</code> - Plex webhook</li>
        <li><code>POST /webhook/jellyfin</code> - Jellyfin webhook</li>
        <li><code>POST /webhook/emby</code> - Emby webhook</li>
        <li><code>POST /webhook/tautulli</code> - Tautulli webhook</li>
        <li><code>POST /api/batch/submit</code> - Submit batch transcription</li>
        <li><code>GET /api/batch/session/{id}</code> - Get batch session status</li>
    </ul>
    
    <h2>Documentation</h2>
    <p>Visit <a href="/docs">/docs</a> for interactive API documentation.</p>
</body>
</html>
        """, status_code=200)
    
    return templates.TemplateResponse(request, "index.html", {
        "settings": settings,
        "version": SUBGEN_AZURE_BATCH_VERSION,
    })


@router.get("/api/config", response_model=ConfigInfo)
async def get_config():
    """Get current configuration (safe subset)."""
    settings = get_settings()
    
    return ConfigInfo(
        azure_configured=settings.azure.is_configured,
        azure_region=settings.azure.speech_region,
        bazarr_configured=settings.bazarr.is_configured,
        plex_configured=settings.plex.is_configured,
        jellyfin_configured=settings.jellyfin.is_configured,
        emby_configured=settings.emby.is_configured,
        media_folders=settings.media_folders,
        subtitle_language=settings.subtitle_language,
        concurrent_transcriptions=settings.concurrent_transcriptions,
        default_theme=settings.default_theme,
    )


@router.get("/api/status", response_model=StatusResponse)
async def check_status():
    """
    Check connectivity to Azure Speech and Storage services.
    
    Returns connection status for both services, including any error messages.
    """
    import aiohttp
    settings = get_settings()
    
    # Check Speech Service
    speech_status = ServiceStatus(configured=False, connected=False)
    if settings.azure.speech_key and settings.azure.speech_region:
        speech_status.configured = True
        try:
            # Test the speech service by getting a token
            url = f"https://{settings.azure.speech_region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
            headers = {"Ocp-Apim-Subscription-Key": settings.azure.speech_key}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        speech_status.connected = True
                    else:
                        speech_status.error = f"HTTP {resp.status}"
        except Exception as e:
            speech_status.error = str(e)[:100]
    
    # Check Blob Storage
    storage_status = ServiceStatus(configured=False, connected=False)
    if settings.azure.storage_connection_string:
        storage_status.configured = True
        if not AZURE_STORAGE_AVAILABLE:
            storage_status.error = "azure-storage-blob package not installed"
        else:
            try:
                client = BlobServiceClient.from_connection_string(  # type: ignore[union-attr]
                    settings.azure.storage_connection_string
                )
                container_client = client.get_container_client(
                    settings.azure.storage_container
                )
                # Just check if we can access the container
                container_client.exists()
                storage_status.connected = True
            except Exception as e:
                error_msg = str(e)
                # Extract meaningful error from Azure errors
                if "KeyBasedAuthenticationNotPermitted" in error_msg:
                    storage_status.error = "Key-based auth disabled on storage account"
                elif "AuthorizationFailure" in error_msg:
                    storage_status.error = "Authorization failed - check connection string"
                elif "ContainerNotFound" in error_msg:
                    storage_status.error = f"Container '{settings.azure.storage_container}' not found"
                else:
                    storage_status.error = error_msg[:80]
    
    return StatusResponse(
        speech=speech_status,
        storage=storage_status,
    )


@router.post("/api/notifications/test")
async def test_notifications():
    """
    Send a test notification to verify notification configuration.
    
    Returns status for each configured notification service.
    """
    from app.notification_service import NotificationService
    
    notifier = NotificationService.get_instance()
    
    if not notifier.config.is_configured:
        return {
            "configured": False,
            "message": "No notification services configured. Set PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN.",
            "results": {}
        }
    
    results = await notifier.test_notification()
    
    return {
        "configured": True,
        "results": results,
    }


@router.get("/api/notifications/config")
async def get_notification_config():
    """
    Get notification configuration status (without sensitive data).
    """
    from app.notification_service import NotificationService
    
    notifier = NotificationService.get_instance()
    config = notifier.config
    
    return {
        "pushover_configured": config.pushover_configured,
        "notify_on_failure": config.notify_on_failure,
    }


@router.get("/api/files", response_model=DirectoryListing)
async def list_files(
    path: str = Query("/", description="Directory path to list"),
):
    """
    List files in a directory.
    
    Only lists directories and video files.
    Also indicates if a subtitle file exists for each video.
    """
    settings = get_settings()
    
    # Validate path is within allowed media folders
    target_path = Path(path).resolve()
    
    # Check if path is within allowed folders
    is_allowed = False
    for folder in settings.media_folders:
        folder_path = Path(folder).resolve()
        try:
            target_path.relative_to(folder_path)
            is_allowed = True
            break
        except ValueError:
            continue
        
        # Also allow listing the root of media folders
        if target_path == folder_path:
            is_allowed = True
            break
    
    # Special case: allow root path to list media folders
    if path == "/" or path == "":
        items = []
        for folder in settings.media_folders:
            folder_path = Path(folder)
            if folder_path.exists():
                items.append(FileInfo(
                    name=folder_path.name or folder,
                    path=str(folder_path),
                    is_dir=True,
                ))
        return DirectoryListing(path="/", parent=None, items=items)
    
    if not is_allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {path} is not within configured media folders"
        )
    
    if not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    
    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    
    items = []
    
    try:
        for entry in sorted(target_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            # Skip hidden files
            if entry.name.startswith('.'):
                continue
            
            if entry.is_dir():
                items.append(FileInfo(
                    name=entry.name,
                    path=str(entry),
                    is_dir=True,
                ))
            elif entry.suffix.lower() in MEDIA_EXTENSIONS:
                # Check for existing subtitle (SRT or LRC)
                is_audio = entry.suffix.lower() in AUDIO_EXTENSIONS
                
                # For audio files, check for LRC; for video, check for SRT
                if is_audio:
                    has_subtitle = any(
                        (entry.parent / f"{entry.stem}{ext}").exists() or
                        (entry.parent / f"{entry.stem}.{settings.subtitle_language}{ext}").exists()
                        for ext in ['.lrc', '.srt']
                    )
                else:
                    has_subtitle = any(
                        (entry.parent / f"{entry.stem}{ext}").exists() or
                        (entry.parent / f"{entry.stem}.{settings.subtitle_language}{ext}").exists()
                        for ext in SUBTITLE_EXTENSIONS
                    )
                
                items.append(FileInfo(
                    name=entry.name,
                    path=str(entry),
                    is_dir=False,
                    size=entry.stat().st_size,
                    has_subtitle=has_subtitle,
                ))
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")
    
    # Calculate parent path
    parent = None
    if target_path != Path("/"):
        parent_path = target_path.parent
        # Check if parent is still within allowed folders or is root
        for folder in settings.media_folders:
            folder_path = Path(folder).resolve()
            try:
                parent_path.relative_to(folder_path)
                parent = str(parent_path)
                break
            except ValueError:
                if parent_path == folder_path:
                    parent = str(parent_path)
                    break
        
        # If parent is not in any media folder, link back to root
        if parent is None:
            parent = "/"
    
    return DirectoryListing(
        path=str(target_path),
        parent=parent,
        items=items,
    )


@router.get("/api/languages")
async def list_languages():
    """List available transcription languages."""
    languages = []
    for lang in LanguageCode:
        if lang == LanguageCode.NONE:
            continue
        languages.append({
            "code": lang.iso_639_1,
            "name": lang.name_en or lang.name.replace("_", " ").title(),
            "azure_locale": lang.to_azure_locale(),
        })
    return {"languages": languages}


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    settings = get_settings()
    
    return {
        "status": "healthy",
        "azure_configured": settings.azure.is_configured,
        "version": SUBGEN_AZURE_BATCH_VERSION,
    }
