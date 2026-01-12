"""
Batch Router - Batch processing API endpoints.

Provides endpoints for the Web UI to:
- Submit files/folders for batch transcription
- Monitor job progress
- Download completed subtitles

Uses the shared TranscriptionService for unified session/job tracking,
so both UI batch jobs and Bazarr ASR jobs appear in the same list.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.bazarr_client import BazarrClient
from app.config import get_settings
from app.skip_checker import should_skip_file
from app.subtitle_utils import get_srt_path
from app.transcription_service import JobSource
from app.transcription_service import JobStatus as ServiceJobStatus
from app.transcription_service import (TranscriptionJob, TranscriptionService,
                                       TranscriptionSession)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/batch", tags=["Batch Processing"])


# Local JobStatus enum for API responses (maps to ServiceJobStatus)
class JobStatus(str, Enum):
    """Batch job status."""
    PENDING = "pending"
    EXTRACTING = "extracting"
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    
    @classmethod
    def from_service_status(cls, status: ServiceJobStatus) -> "JobStatus":
        """Convert from TranscriptionService status."""
        mapping = {
            ServiceJobStatus.PENDING: cls.PENDING,
            ServiceJobStatus.EXTRACTING: cls.EXTRACTING,
            ServiceJobStatus.UPLOADING: cls.UPLOADING,
            ServiceJobStatus.TRANSCRIBING: cls.TRANSCRIBING,
            ServiceJobStatus.COMPLETED: cls.COMPLETED,
            ServiceJobStatus.FAILED: cls.FAILED,
            ServiceJobStatus.CANCELLED: cls.CANCELLED,
        }
        return mapping.get(status, cls.PENDING)


@dataclass
class BatchJob:
    """Represents a single file in a batch."""
    id: str
    file_path: str
    language: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    error: Optional[str] = None
    srt_path: Optional[str] = None
    azure_job_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


@dataclass
class BatchSession:
    """A batch processing session containing multiple jobs."""
    id: str
    jobs: Dict[str, BatchJob] = field(default_factory=dict)
    skipped: List[dict] = field(default_factory=list)  # Track skipped files
    created_at: datetime = field(default_factory=datetime.now)
    notify_bazarr: bool = True


# Use TranscriptionService's session storage for unified tracking
# This way both UI batch jobs and Bazarr ASR jobs appear in the same list
def _get_sessions() -> Dict[str, TranscriptionSession]:
    """Get the shared session storage from TranscriptionService."""
    return TranscriptionService._sessions


# Local cache for batch-specific data (skipped files, etc.)
# Maps session_id -> {skipped: [], notify_bazarr: bool}
_batch_metadata: Dict[str, dict] = {}


# Request/Response models
class BatchSubmitRequest(BaseModel):
    """Request to submit files for batch processing."""
    files: List[str] = []  # List of file paths
    folders: List[str] = []  # List of folder paths (will expand to files)
    language: str = "en"
    notify_bazarr: bool = True
    # UI checkbox: "Skip files with existing subtitles"
    # When True, applies skip_checker logic (env var controlled)
    # When False, bypasses all skip checks
    skip_if_exists: bool = True  # Backwards-compatible name for UI
    apply_skip_config: bool = True  # Alias for skip_if_exists
    
    @property
    def should_apply_skip_logic(self) -> bool:
        """Determine if skip logic should run (either field name works)."""
        # If explicitly set to False via either name, don't skip
        return self.skip_if_exists and self.apply_skip_config


class BatchSubmitResponse(BaseModel):
    """Response from batch submission."""
    session_id: str
    job_count: int
    jobs: List[dict]
    skipped: List[dict] = []  # Files that were skipped with reasons


class JobStatusResponse(BaseModel):
    """Status of a single job."""
    id: str
    file_path: str
    status: str
    status_text: str = ""  # Human-readable status description
    progress: int
    error: Optional[str] = None
    srt_path: Optional[str] = None


class SessionStatusResponse(BaseModel):
    """Status of a batch session."""
    session_id: str
    source: str = "ui"  # Source of the session: ui, bazarr, api, webhook
    total_jobs: int
    pending: int
    in_progress: int
    completed: int
    failed: int
    cancelled: int = 0
    jobs: List[JobStatusResponse]
    skipped: List[dict] = []  # Skipped files from submission


def get_status_text(status: JobStatus, progress: int) -> str:
    """Get human-readable status text for a job."""
    if status == JobStatus.PENDING:
        return "Waiting..."
    elif status == JobStatus.EXTRACTING:
        return "Extracting audio"
    elif status == JobStatus.UPLOADING:
        return "Uploading to Azure"
    elif status == JobStatus.TRANSCRIBING:
        return "Transcribing"
    elif status == JobStatus.COMPLETED:
        return "Completed"
    elif status == JobStatus.FAILED:
        return "Failed"
    elif status == JobStatus.CANCELLED:
        return "Cancelled"
    return ""


async def _notify_bazarr_for_completed_jobs(session_id: str, session: TranscriptionSession):
    """
    Notify Bazarr about completed transcriptions using smart path-based lookup.
    
    Instead of a full disk scan, this:
    1. Collects all completed file paths (excluding audio files - Bazarr is for video only)
    2. Looks up unique series/movie IDs by path
    3. Triggers targeted scans for each unique series/movie
    
    Falls back to full disk scan if no specific items found.
    """
    from app.audio_extractor import is_audio_file
    
    settings = get_settings()
    bazarr = BazarrClient(settings.bazarr.url, settings.bazarr.api_key)
    
    try:
        # Collect completed file paths, excluding audio files (Bazarr is for video subtitles only)
        completed_paths = [
            job.file_path for job in session.jobs.values()
            if job.status == JobStatus.COMPLETED and not is_audio_file(job.file_path)
        ]
        
        if not completed_paths:
            logger.debug(f"[{session_id}] No completed video jobs, skipping Bazarr notification")
            return
        
        # Track unique series/movie IDs to avoid duplicate scans
        scanned_series: set = set()
        scanned_movies: set = set()
        
        for path in completed_paths:
            # Try to find matching series
            series = await bazarr.search_series_by_path(path)
            if series:
                series_id = series.get('sonarrSeriesId')
                if series_id and series_id not in scanned_series:
                    await bazarr.trigger_series_scan(series_id)
                    scanned_series.add(series_id)
                    logger.info(f"[{session_id}] Bazarr: Triggered disk scan for series {series_id}")
                continue
            
            # Try to find matching movie
            movie = await bazarr.search_movie_by_path(path)
            if movie:
                movie_id = movie.get('radarrId')
                if movie_id and movie_id not in scanned_movies:
                    await bazarr.trigger_movie_scan(movie_id)
                    scanned_movies.add(movie_id)
                    logger.info(f"[{session_id}] Bazarr: Triggered disk scan for movie {movie_id}")
        
        total_scans = len(scanned_series) + len(scanned_movies)
        if total_scans > 0:
            logger.info(
                f"[{session_id}] Notified Bazarr: {len(scanned_series)} series, "
                f"{len(scanned_movies)} movies"
            )
        else:
            # Fallback to full scan if no specific items found
            # (e.g., files not in Bazarr's library yet)
            logger.info(f"[{session_id}] No matching Bazarr items found, triggering full scan")
            await bazarr.trigger_disk_scan()
            
    except Exception as e:
        logger.warning(f"[{session_id}] Failed to notify Bazarr: {e}")
    finally:
        await bazarr.close()


async def process_batch_job(session_id: str, job_id: str):
    """Process a single job in a batch session using TranscriptionService."""
    session = TranscriptionService.get_session(session_id)
    if not session:
        logger.error(f"Session not found: {session_id}")
        return
    
    job = session.jobs.get(job_id)
    if not job:
        logger.error(f"Job not found: {session_id}/{job_id}")
        return
    
    try:
        # Use TranscriptionService to process the job
        # The service handles: audio extraction, upload, transcription, cleanup
        result, updated_job = await TranscriptionService.transcribe_file(
            file_path=job.file_path,
            language=job.language,
            source=JobSource.UI,
            session_id=session_id,
            job_id=job_id,  # Use existing job
            save_srt=True,
        )
        
        # result is None if job was cancelled
        if result is not None:
            logger.info(f"[{job_id}] Transcription complete: {len(result.segments)} segments")
        
    except Exception as e:
        logger.exception(f"[{job_id}] Failed: {e}")
        # TranscriptionService already marks job as failed


async def process_batch_session(session_id: str):
    """Process all jobs in a batch session."""
    session = TranscriptionService.get_session(session_id)
    if not session:
        logger.error(f"Session not found for processing: {session_id}")
        return
    
    settings = get_settings()
    metadata = _batch_metadata.get(session_id, {})
    
    # Process jobs with concurrency limit
    semaphore = asyncio.Semaphore(settings.concurrent_transcriptions)
    
    async def process_with_semaphore(job_id: str):
        async with semaphore:
            await process_batch_job(session_id, job_id)
    
    # Start all jobs
    tasks = [process_with_semaphore(job_id) for job_id in session.jobs.keys()]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Notify Bazarr if configured (smart scan based on completed files)
    notify_bazarr = metadata.get('notify_bazarr', True)
    if notify_bazarr and settings.bazarr.is_configured:
        await _notify_bazarr_for_completed_jobs(session_id, session)


@router.post("/submit", response_model=BatchSubmitResponse)
async def submit_batch(request: BatchSubmitRequest, background_tasks: BackgroundTasks):
    """
    Submit files for batch transcription.
    
    Args:
        request: Batch submission request with file paths and options.
    
    Returns:
        Session ID and job information.
    """
    # Import media extensions from audio_extractor for consistency
    from app.audio_extractor import (AUDIO_EXTENSIONS, MEDIA_EXTENSIONS,
                                     VIDEO_EXTENSIONS)

    # Expand folders to individual files
    all_files = list(request.files)
    for folder_path in request.folders:
        folder = Path(folder_path)
        if folder.exists() and folder.is_dir():
            # Recursively find all media files (video and audio)
            for ext in MEDIA_EXTENSIONS:
                all_files.extend(str(f) for f in folder.rglob(f"*{ext}"))
    
    if not all_files:
        raise HTTPException(status_code=400, detail="No files or folders provided")
    
    # Track skipped files and valid files
    jobs_info = []
    skipped_files = []
    skipped_not_found = 0
    skipped_not_video = 0
    skipped_by_config = 0
    valid_files = []
    
    for file_path in all_files:
        path = Path(file_path)
        
        # Validate file exists
        if not path.exists():
            logger.warning(f"File not found: {file_path}")
            skipped_not_found += 1
            skipped_files.append({"file_path": file_path, "reason": "File not found"})
            continue
        
        # Skip non-media files (must be video or audio)
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            logger.warning(f"Skipping non-media file: {file_path}")
            skipped_not_video += 1
            skipped_files.append({"file_path": file_path, "reason": "Not a media file"})
            continue
        
        # Apply skip configuration if enabled (UI checkbox controls this)
        # This checks: existing subtitles, internal subs, audio language, etc.
        if request.should_apply_skip_logic:
            skip_result = await should_skip_file(file_path, request.language)
            if skip_result.should_skip:
                logger.info(f"Skip config: {path.name} - {skip_result.reason}")
                skipped_by_config += 1
                skipped_files.append({"file_path": file_path, "reason": skip_result.reason or "Skipped by configuration"})
                continue
        
        valid_files.append(file_path)
    
    if not valid_files:
        # Provide descriptive error message based on why files were skipped
        total_skipped = skipped_not_found + skipped_not_video + skipped_by_config
        if skipped_by_config == len(all_files):
            raise HTTPException(status_code=400, detail="All selected files were skipped (check skip configuration)")
        elif skipped_by_config > 0 and skipped_by_config == total_skipped:
            raise HTTPException(status_code=400, detail=f"All {skipped_by_config} file(s) skipped by skip configuration")
        elif skipped_not_found == len(all_files):
            raise HTTPException(status_code=400, detail="All selected files were not found")
        elif skipped_not_video == len(all_files):
            raise HTTPException(status_code=400, detail="No video files selected")
        else:
            parts = []
            if skipped_by_config > 0:
                parts.append(f"{skipped_by_config} skipped by configuration")
            if skipped_not_found > 0:
                parts.append(f"{skipped_not_found} not found")
            if skipped_not_video > 0:
                parts.append(f"{skipped_not_video} not video files")
            raise HTTPException(status_code=400, detail=f"No valid files to process ({', '.join(parts)})")
    
    # Create session using TranscriptionService
    session = await TranscriptionService.create_session(
        source=JobSource.UI,
        notify_bazarr=request.notify_bazarr
    )
    
    # Add jobs for each valid file
    for file_path in valid_files:
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path=file_path,
            language=request.language,
            source=JobSource.UI,
        )
        jobs_info.append({
            "id": job.id,
            "file_path": file_path,
            "status": JobStatus.from_service_status(job.status).value,
        })
    
    # Store batch-specific metadata (skipped files, notify_bazarr)
    _batch_metadata[session.id] = {
        'skipped': skipped_files,
        'notify_bazarr': request.notify_bazarr,
    }
    
    # Start background processing
    background_tasks.add_task(process_batch_session, session.id)
    
    logger.info(f"Created batch session {session.id} with {len(session.jobs)} jobs, {len(skipped_files)} skipped")
    
    return BatchSubmitResponse(
        session_id=session.id,
        job_count=len(session.jobs),
        jobs=jobs_info,
        skipped=skipped_files,
    )


@router.get("/session/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: str):
    """Get status of a batch session."""
    session = TranscriptionService.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    metadata = _batch_metadata.get(session_id, {})
    
    # Count jobs by status (convert from service status to local status)
    pending = sum(1 for j in session.jobs.values() 
                  if j.status == ServiceJobStatus.PENDING)
    in_progress = sum(1 for j in session.jobs.values() 
                      if j.status in (ServiceJobStatus.EXTRACTING, ServiceJobStatus.UPLOADING, ServiceJobStatus.TRANSCRIBING))
    completed = sum(1 for j in session.jobs.values() 
                    if j.status == ServiceJobStatus.COMPLETED)
    failed = sum(1 for j in session.jobs.values() 
                 if j.status == ServiceJobStatus.FAILED)
    cancelled = sum(1 for j in session.jobs.values() 
                    if j.status == ServiceJobStatus.CANCELLED)
    
    jobs = [
        JobStatusResponse(
            id=job.id,
            file_path=job.file_path,
            status=JobStatus.from_service_status(job.status).value,
            status_text=get_status_text(JobStatus.from_service_status(job.status), 0),
            progress=0,
            error=job.error,
            srt_path=job.srt_path,
        )
        for job in session.jobs.values()
    ]
    
    # Get session source (fallback to 'ui' for backwards compatibility)
    session_source = session.source.value if hasattr(session, 'source') else "ui"
    
    return SessionStatusResponse(
        session_id=session_id,
        source=session_source,
        total_jobs=len(session.jobs),
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        failed=failed,
        cancelled=cancelled,
        jobs=jobs,
        skipped=metadata.get('skipped', []),
    )


@router.get("/job/{session_id}/{job_id}", response_model=JobStatusResponse)
async def get_job_status(session_id: str, job_id: str):
    """Get status of a specific job."""
    session = TranscriptionService.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    job = session.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    local_status = JobStatus.from_service_status(job.status)
    return JobStatusResponse(
        id=job.id,
        file_path=job.file_path,
        status=local_status.value,
        status_text=get_status_text(local_status, 0),
        progress=0,
        error=job.error,
        srt_path=job.srt_path,
    )


@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a batch session."""
    session = TranscriptionService.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    await TranscriptionService.delete_session(session_id)
    if session_id in _batch_metadata:
        del _batch_metadata[session_id]
    return {"status": "deleted", "session_id": session_id}


@router.post("/session/{session_id}/cancel")
async def cancel_session(session_id: str):
    """
    Cancel a batch session.
    
    Marks all pending/in-progress jobs as cancelled and cleans up Azure resources
    (uploaded blobs and transcription jobs).
    """
    session = TranscriptionService.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        result = await TranscriptionService.cancel_session(session_id)
        return {
            "status": "cancelled",
            "session_id": session_id,
            "cancelled_jobs": result["cancelled"],
            "cleaned_blobs": result["cleaned_blobs"],
            "errors": result["errors"],
        }
    except Exception as e:
        logger.error(f"Failed to cancel session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions")
async def list_sessions():
    """List all active batch sessions with full job details (includes Bazarr jobs)."""
    sessions = []
    all_sessions = TranscriptionService.list_all_sessions()
    
    for session in all_sessions:
        completed = sum(1 for j in session.jobs.values() 
                        if j.status == ServiceJobStatus.COMPLETED)
        failed = sum(1 for j in session.jobs.values() 
                     if j.status == ServiceJobStatus.FAILED)
        cancelled = sum(1 for j in session.jobs.values() 
                        if j.status == ServiceJobStatus.CANCELLED)
        
        metadata = _batch_metadata.get(session.id, {})
        
        # Include full job details for UI restoration
        jobs = []
        for job_id, job in session.jobs.items():
            local_status = JobStatus.from_service_status(job.status)
            jobs.append({
                "id": job_id,
                "file_path": job.file_path,
                "status": local_status.value,
                "status_text": get_status_text(local_status, 0),
                "error": job.error,
                "source": job.source.value if hasattr(job, 'source') else "ui",
            })
        
        sessions.append({
            "session_id": session.id,
            "source": session.source.value if hasattr(session, 'source') else "ui",
            "total_jobs": len(session.jobs),
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "created_at": session.created_at.isoformat(),
            "jobs": jobs,
        })
    
    return {"sessions": sessions}
