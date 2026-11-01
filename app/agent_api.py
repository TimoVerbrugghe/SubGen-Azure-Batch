"""Authenticated REST and MCP interfaces for agent-submitted batches."""

import asyncio
import hmac
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings, require_azure_configured
from app.transcription_service import (
    JobSource,
    JobStatus,
    TranscriptionJob,
    TranscriptionService,
    TranscriptionSession,
)
from app.utils.audio_extractor import MEDIA_EXTENSIONS
from app.utils.language_code import LanguageCode
from app.utils.skip_checker import should_skip_file

logger = logging.getLogger(__name__)

MAX_AGENT_BATCH_FILES = 1000
MAX_AGENT_BATCH_LANGUAGES = 4
_LOCALE_PATTERN = re.compile(r"^([a-zA-Z]{2,3})(?:-([a-zA-Z]{2}|[0-9]{3}))?$")
_agent_batch_tasks: dict[str, asyncio.Task[None]] = {}


def _get_batch_router() -> ModuleType:
    """Load the legacy batch module lazily to avoid router-package cycles."""
    from app.routers import batch

    return batch


class AgentBatchSubmitRequest(BaseModel):
    """Request to submit media paths and languages for agent transcription."""

    files: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)
    languages: list[str]
    apply_skip_rules: bool = True
    notify_bazarr: bool = False


class AgentJobResponse(BaseModel):
    """Safe per-file job data for agent-facing APIs."""

    id: str
    file_path: str
    language: str
    status: str
    stage: str
    status_text: str
    progress: int = 0
    error: str | None = None
    srt_path: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class AgentBatchSummary(BaseModel):
    """Summary counts for a batch session."""

    session_id: str
    total_jobs: int
    pending: int
    in_progress: int
    completed: int
    failed: int
    cancelled: int
    created_at: str


class AgentBatchSubmitResponse(BaseModel):
    """Response from agent batch submission."""

    session_id: str
    job_count: int
    jobs: list[AgentJobResponse]
    skipped: list[dict] = Field(default_factory=list)


class AgentBatchListResponse(BaseModel):
    """Active agent batches."""

    batches: list[AgentBatchSummary]


class AgentBatchStatusResponse(AgentBatchSummary):
    """Detailed per-file status for an agent batch."""

    skipped: list[dict] = Field(default_factory=list)
    jobs: list[AgentJobResponse]


class AgentBatchCancelResponse(BaseModel):
    """Cancellation result for an agent batch."""

    session_id: str
    status: str
    cancelled_jobs: int
    cleaned_blobs: int
    errors: list[str]


async def require_agent_api_key(
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> None:
    """Require a configured agent API key and compare it in constant time."""
    configured_key = get_configured_agent_api_key()
    if not configured_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Agent API is disabled until AGENT_API_KEY is configured "
                "with at least 32 non-whitespace characters."
            ),
        )
    if not hmac.compare_digest(x_api_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def get_configured_agent_api_key() -> str | None:
    """Return a sufficiently long configured key or ``None`` to fail closed."""
    api_key = get_settings().agent_api_key
    if sum(not character.isspace() for character in api_key) < 32:
        return None
    return api_key


router = APIRouter(
    prefix="/api/agent/batches",
    tags=["Agent Batches"],
    dependencies=[Depends(require_agent_api_key)],
)


def _normalize_languages(languages: Sequence[str]) -> list[str]:
    """Validate language codes and return Azure-compatible locale strings."""
    if not languages:
        raise ValueError("Provide at least one language.")
    if len(languages) > MAX_AGENT_BATCH_LANGUAGES:
        raise ValueError(
            f"At most {MAX_AGENT_BATCH_LANGUAGES} languages may be requested per batch."
        )

    normalized = []
    seen = set()
    for language in languages:
        value = language.strip()
        match = _LOCALE_PATTERN.fullmatch(value)
        if not match:
            raise ValueError(f"Invalid language code or Azure locale: {language!r}.")
        language_code = LanguageCode.from_string(match.group(1))
        if language_code is LanguageCode.NONE:
            raise ValueError(
                f"Unsupported language code or Azure locale: {language!r}."
            )

        region = match.group(2)
        locale = (
            f"{language_code.iso_639_1 or language_code.iso_639_2_t}-{region.upper()}"
            if region
            else language_code.to_azure_locale()
        )
        key = locale.casefold()
        if key in seen:
            raise ValueError(f"Duplicate language requested: {language!r}.")
        seen.add(key)
        normalized.append(locale)
    return normalized


def _is_within_root(path: Path, root: Path) -> bool:
    """Return whether a resolved path is equal to or below an allowed root."""
    return path == root or root in path.parents


def _collect_media_paths(
    files: Sequence[str], folders: Sequence[str], configured_roots: Sequence[str]
) -> list[str]:
    """Resolve and expand agent paths without escaping configured media roots."""
    roots = []
    for configured_root in configured_roots:
        try:
            root = Path(configured_root).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if root.is_dir():
            roots.append(root)
    if not roots:
        raise ValueError("No configured media folders are currently available.")

    paths: dict[str, Path] = {}

    def resolve_input(raw_path: str, expected_kind: str) -> Path:
        if not raw_path or "\x00" in raw_path:
            raise ValueError("Media paths must be non-empty absolute paths.")
        candidate = Path(raw_path)
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(
                "Media paths must be absolute and cannot contain traversal."
            )
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError(
                "A requested media path does not exist or cannot be resolved."
            )
        if not any(_is_within_root(resolved, root) for root in roots):
            raise ValueError(
                "A requested media path is outside configured media folders."
            )
        if expected_kind == "file":
            if (
                not resolved.is_file()
                or resolved.suffix.lower() not in MEDIA_EXTENSIONS
            ):
                raise ValueError("Each file path must refer to a supported media file.")
        elif not resolved.is_dir():
            raise ValueError("Each folder path must refer to a directory.")
        return resolved

    for file_path in files:
        resolved = resolve_input(file_path, "file")
        paths[str(resolved)] = resolved

    for folder_path in folders:
        folder = resolve_input(folder_path, "folder")
        pending_directories = [folder]
        visited_directories = set()
        while pending_directories:
            current_directory = pending_directories.pop()
            if current_directory in visited_directories:
                continue
            visited_directories.add(current_directory)
            try:
                children = current_directory.iterdir()
                for candidate in children:
                    try:
                        resolved = candidate.resolve(strict=True)
                    except (OSError, RuntimeError):
                        continue
                    if not any(_is_within_root(resolved, root) for root in roots):
                        continue
                    if resolved.is_dir():
                        pending_directories.append(resolved)
                    elif (
                        resolved.is_file()
                        and resolved.suffix.lower() in MEDIA_EXTENSIONS
                    ):
                        paths[str(resolved)] = resolved
                        if len(paths) > MAX_AGENT_BATCH_FILES:
                            raise ValueError(
                                f"A batch may contain at most "
                                f"{MAX_AGENT_BATCH_FILES} media files."
                            )
            except OSError as exc:
                raise ValueError("A requested media folder cannot be read.") from exc

    if len(paths) > MAX_AGENT_BATCH_FILES:
        raise ValueError(
            f"A batch may contain at most {MAX_AGENT_BATCH_FILES} media files."
        )
    if not paths:
        raise ValueError("No supported media files were found in the requested paths.")
    return sorted(paths)


def _job_response(job: TranscriptionJob) -> AgentJobResponse:
    """Convert a service job to a safe agent-facing response."""
    stage = "queued" if job.status == JobStatus.PENDING else job.status.value
    status_text = "Queued" if job.status == JobStatus.PENDING else job.get_status_text()
    return AgentJobResponse(
        id=job.id,
        file_path=job.file_path,
        language=job.language,
        status=job.status.value,
        stage=stage,
        status_text=status_text,
        progress=job.progress,
        error=job.error,
        srt_path=job.srt_path,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


def _session_counts(session: TranscriptionSession) -> dict[str, str | int]:
    """Count job states for an agent batch response."""
    return {
        "session_id": session.id,
        "total_jobs": len(session.jobs),
        "pending": sum(
            job.status == JobStatus.PENDING for job in session.jobs.values()
        ),
        "in_progress": sum(
            job.status
            in (JobStatus.EXTRACTING, JobStatus.UPLOADING, JobStatus.TRANSCRIBING)
            for job in session.jobs.values()
        ),
        "completed": sum(
            job.status == JobStatus.COMPLETED for job in session.jobs.values()
        ),
        "failed": sum(job.status == JobStatus.FAILED for job in session.jobs.values()),
        "cancelled": sum(
            job.status == JobStatus.CANCELLED for job in session.jobs.values()
        ),
        "created_at": session.created_at.isoformat(),
    }


def _start_batch_task(session_id: str) -> None:
    """Start and track processing for an agent batch."""
    batch_router = _get_batch_router()
    task = asyncio.create_task(
        batch_router.process_batch_session(session_id),
        name=f"agent-batch-{session_id}",
    )
    _agent_batch_tasks[session_id] = task

    def on_done(completed_task: asyncio.Task[None]) -> None:
        if _agent_batch_tasks.get(session_id) is completed_task:
            _agent_batch_tasks.pop(session_id, None)
        if not completed_task.cancelled():
            error = completed_task.exception()
            if error:
                logger.error("Agent batch %s failed: %s", session_id, error)

    task.add_done_callback(on_done)


async def submit_agent_batch(
    request: AgentBatchSubmitRequest,
) -> AgentBatchSubmitResponse:
    """Validate, create, and schedule an agent-submitted transcription batch."""
    require_azure_configured()
    if not request.files and not request.folders:
        raise HTTPException(
            status_code=400, detail="Provide at least one file or folder."
        )
    if len(request.files) + len(request.folders) > MAX_AGENT_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A request may contain at most {MAX_AGENT_BATCH_FILES} paths.",
        )

    try:
        languages = _normalize_languages(request.languages)
        media_paths = await asyncio.to_thread(
            _collect_media_paths,
            request.files,
            request.folders,
            get_settings().media_folders,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(media_paths) * len(languages) > MAX_AGENT_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A batch may contain at most {MAX_AGENT_BATCH_FILES} "
                "file-language jobs."
            ),
        )

    valid_jobs = []
    skipped = []
    for file_path in media_paths:
        for language in languages:
            if request.apply_skip_rules:
                skip_result = await should_skip_file(file_path, language)
                if skip_result.should_skip:
                    skipped.append(
                        {
                            "file_path": file_path,
                            "language": language,
                            "reason": skip_result.reason or "Skipped by configuration",
                        }
                    )
                    continue
            valid_jobs.append((file_path, language))

    if not valid_jobs:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "No media files remain after applying skip rules.",
                "skipped": skipped,
            },
        )

    session = await TranscriptionService.create_session(
        source=JobSource.API,
        notify_bazarr=request.notify_bazarr,
    )
    batch_router = _get_batch_router()
    batch_router._batch_metadata[session.id] = {
        "skipped": skipped,
        "notify_bazarr": request.notify_bazarr,
    }
    try:
        jobs = []
        for file_path, language in valid_jobs:
            job = await TranscriptionService.add_job(
                session.id,
                file_path,
                language,
                JobSource.API,
            )
            jobs.append(_job_response(job))
        _start_batch_task(session.id)
    except Exception:
        batch_router._batch_metadata.pop(session.id, None)
        await TranscriptionService.delete_session(session.id)
        raise

    return AgentBatchSubmitResponse(
        session_id=session.id,
        job_count=len(jobs),
        jobs=jobs,
        skipped=skipped,
    )


def _get_agent_session(session_id: str) -> TranscriptionSession:
    """Return an agent batch, hiding non-agent sessions from this interface."""
    session = TranscriptionService.get_session(session_id)
    if not session or session.source != JobSource.API:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return session


async def get_agent_batch_status(session_id: str) -> AgentBatchStatusResponse:
    """Return aggregate and detailed file status for an agent batch."""
    session = _get_agent_session(session_id)
    batch_router = _get_batch_router()
    return AgentBatchStatusResponse(
        **_session_counts(session),
        skipped=batch_router._batch_metadata.get(session_id, {}).get("skipped", []),
        jobs=[_job_response(job) for job in session.jobs.values()],
    )


async def list_active_agent_batches() -> AgentBatchListResponse:
    """List agent batches with queued or in-progress transcription jobs."""
    sessions = [
        session
        for session in TranscriptionService.list_all_sessions()
        if session.source == JobSource.API
        and any(
            job.status
            in (
                JobStatus.PENDING,
                JobStatus.EXTRACTING,
                JobStatus.UPLOADING,
                JobStatus.TRANSCRIBING,
            )
            for job in session.jobs.values()
        )
    ]
    return AgentBatchListResponse(
        batches=[AgentBatchSummary(**_session_counts(session)) for session in sessions]
    )


async def cancel_agent_batch(session_id: str) -> AgentBatchCancelResponse:
    """Cancel an agent batch and stop its tracked worker task."""
    require_azure_configured()
    session = _get_agent_session(session_id)
    cleanup_error = None
    try:
        result = await TranscriptionService.cancel_session(session.id)
    except Exception as exc:
        logger.exception(
            "Failed to clean up Azure resources for agent batch %s", session.id
        )
        cleanup_error = str(exc)
        result = {
            "cancelled": sum(
                job.status == JobStatus.CANCELLED for job in session.jobs.values()
            ),
            "cleaned_blobs": 0,
            "errors": [cleanup_error],
        }
    finally:
        task = _agent_batch_tasks.get(session.id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    if cleanup_error:
        result["errors"].append(
            "The batch worker was stopped, but some Azure resources may "
            "require cleanup."
        )
    return AgentBatchCancelResponse(
        session_id=session.id,
        status="cancelled",
        cancelled_jobs=result["cancelled"],
        cleaned_blobs=result["cleaned_blobs"],
        errors=result["errors"],
    )


@router.post("", response_model=AgentBatchSubmitResponse)
async def submit_agent_batch_endpoint(
    request: AgentBatchSubmitRequest,
) -> AgentBatchSubmitResponse:
    """Submit media paths for one or more requested transcription languages."""
    require_azure_configured()
    return await submit_agent_batch(request)


@router.get("", response_model=AgentBatchListResponse)
async def list_agent_batches_endpoint() -> AgentBatchListResponse:
    """List active agent-submitted batches."""
    return await list_active_agent_batches()


@router.get("/{session_id}", response_model=AgentBatchStatusResponse)
async def get_agent_batch_endpoint(session_id: str) -> AgentBatchStatusResponse:
    """Get batch progress and per-file processing stages."""
    return await get_agent_batch_status(session_id)


@router.post("/{session_id}/cancel")
async def cancel_agent_batch_endpoint(
    session_id: str,
) -> AgentBatchCancelResponse:
    """Cancel an agent batch and clean up its active transcription resources."""
    require_azure_configured()
    return await cancel_agent_batch(session_id)
