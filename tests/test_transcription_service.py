"""
Tests for transcription service module.

Tests cover:
- TranscriptionJob dataclass
- TranscriptionSession management
- Session/Job lifecycle
- Status updates and tracking
"""

import asyncio
import io
import wave
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.transcription_service import (
    JobSource,
    JobStatus,
    PrioritySemaphore,
    TranscriptionJob,
    TranscriptionService,
)
from app.utils.azure_batch_transcriber import TranscriptionResult


@pytest.fixture
def file_pipeline(
    mock_settings, patched_settings, mock_transcription_result, tmp_path, monkeypatch
):
    """Isolate service state and mock external transcription/refresh boundaries."""
    import app.transcription_service as service
    from app.utils.media_server_client import MediaRefreshResult

    mock_settings.max_concurrent_uploads = 2
    mock_settings.job_poll_interval = 1
    mock_settings.transcription_timeout = 10
    transcriber = MagicMock()
    transcriber.upload_audio = AsyncMock(
        return_value=("https://storage.invalid/audio", "blob")
    )
    azure_job = SimpleNamespace(
        id="azure-job", locale="en-US", status=SimpleNamespace(value="Succeeded")
    )
    transcriber.create_transcription = AsyncMock(return_value=azure_job)
    transcriber.get_transcription_status = AsyncMock(return_value=azure_job)
    transcriber.get_transcription_result = AsyncMock(
        return_value=mock_transcription_result
    )
    transcriber.delete_transcription = AsyncMock()
    transcriber.delete_blob = AsyncMock()
    transcriber.close = AsyncMock()
    audio = tmp_path / "extracted.ogg"
    audio.write_bytes(b"audio")
    extractor = AsyncMock(return_value=str(audio))
    refresher = MagicMock()
    refresher.refresh = AsyncMock(
        return_value={
            "plex": MediaRefreshResult("queued"),
            "jellyfin": MediaRefreshResult("failed", "HTTP 401"),
        }
    )
    monkeypatch.setattr(service.TranscriptionService, "_sessions", {})
    monkeypatch.setattr(service.TranscriptionService, "_lock", asyncio.Lock())
    for name in ("_pipeline_semaphore", "_upload_semaphore", "_azure_semaphore"):
        monkeypatch.setattr(service.TranscriptionService, name, None)
    with (
        patch.object(service, "get_settings", return_value=mock_settings),
        patch.object(service, "extract_audio", new=extractor),
        patch.object(service, "AzureBatchTranscriber", return_value=transcriber),
        patch.object(
            service, "MediaServerRefresher", return_value=refresher
        ) as refresh_factory,
    ):
        yield service, transcriber, refresher, refresh_factory, extractor


class TestPostSaveRefresh:
    """Verify refresh timing and independent outcomes through the public service."""

    @pytest.mark.asyncio
    async def test_should_refresh_only_after_subtitle_is_persisted_and_keep_completed_status(
        self, file_pipeline, tmp_path
    ):
        from app.utils.media_server_client import (
            MediaRefreshContext,
            MediaRefreshResult,
        )

        service, transcriber, refresher, factory, extractor = file_pipeline
        media_path = str(tmp_path / "episode.mkv")
        context = MediaRefreshContext(item_ids={"plex": "123"})
        session = await service.TranscriptionService.create_session()

        async def confirm_saved(path, carried_context):
            jobs = list(session.jobs.values())
            job = jobs[0]
            assert path == media_path
            assert carried_context is context
            assert Path(job.srt_path).read_text().startswith("1\n")
            transcriber.close.assert_awaited_once()
            return {
                "plex": MediaRefreshResult("queued"),
                "jellyfin": MediaRefreshResult("failed", "HTTP 401"),
            }

        refresher.refresh.side_effect = confirm_saved
        result, job = await service.TranscriptionService.transcribe_file(
            media_path,
            "en",
            session_id=session.id,
            media_refresh_context=context,
            source=service.JobSource.WEBHOOK,
            audio_track=1,
        )
        assert result is not None
        assert job.status == service.JobStatus.COMPLETED
        assert job.error is None
        assert job.to_dict()["media_refresh_results"] == {
            "plex": {"status": "queued", "error": None},
            "jellyfin": {"status": "failed", "error": "HTTP 401"},
        }
        assert job.media_refresh_status == {"plex": True, "jellyfin": False}
        extractor.assert_awaited_once_with(
            media_path, output_format="ogg", audio_track=1
        )
        factory.assert_called_once_with()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario",
        [
            "response_only",
            "audio",
            "save_failure",
            "cancelled",
            "transcription_failure",
        ],
    )
    async def test_should_not_refresh_without_a_successfully_saved_video_subtitle(
        self, scenario, file_pipeline, tmp_path
    ):
        service, transcriber, refresher, factory, _ = file_pipeline
        path = str(tmp_path / ("audio.mp3" if scenario == "audio" else "movie.mkv"))
        session = await service.TranscriptionService.create_session()
        if scenario == "transcription_failure":
            transcriber.create_transcription.side_effect = RuntimeError(
                "transcription unavailable"
            )
        if scenario == "cancelled":

            async def cancel_before_save(*args, **kwargs):
                await service.TranscriptionService.cancel_session(session.id)
                return MagicMock()

            transcriber.get_transcription_result.side_effect = cancel_before_save

        with patch(
            "app.utils.subtitle_utils.save_srt",
            side_effect=OSError("subtitle write failed")
            if scenario == "save_failure"
            else None,
        ) as save:
            if scenario in ("save_failure", "transcription_failure"):
                with pytest.raises(
                    OSError if scenario == "save_failure" else RuntimeError,
                    match="subtitle write failed"
                    if scenario == "save_failure"
                    else "transcription unavailable",
                ):
                    await service.TranscriptionService.transcribe_file(
                        path, "en", session_id=session.id
                    )
                assert (
                    next(iter(session.jobs.values())).status == service.JobStatus.FAILED
                )
            else:
                result, job = await service.TranscriptionService.transcribe_file(
                    path,
                    "en",
                    session_id=session.id,
                    save_srt=scenario != "response_only",
                )
                if scenario == "audio":
                    assert job.srt_path.endswith(".lrc")
                    assert Path(job.srt_path).exists()
                elif scenario == "cancelled":
                    assert result is None
                    assert job.status == service.JobStatus.CANCELLED
                else:
                    assert job.srt_path is None
                save.assert_not_called()
        refresher.refresh.assert_not_awaited()
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_reuse_session_refresher_for_each_saved_episode(
        self, file_pipeline, tmp_path
    ):
        service, _, refresher, factory, _ = file_pipeline
        session = await service.TranscriptionService.create_session()
        for episode in ("ep1.mkv", "ep2.mkv"):
            await service.TranscriptionService.transcribe_file(
                str(tmp_path / episode), "en", session_id=session.id
            )
        assert [call.args[0] for call in refresher.refresh.call_args_list] == [
            str(tmp_path / "ep1.mkv"),
            str(tmp_path / "ep2.mkv"),
        ]
        factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_process_batch_without_end_of_session_duplicate_refresh(
        self, file_pipeline, tmp_path, mock_settings, app_client
    ):
        from app.routers import batch

        service, _, refresher, factory, _ = file_pipeline
        mock_settings.bazarr.is_configured = False
        session = await service.TranscriptionService.create_session()
        for episode in ("one.mkv", "two.mkv"):
            await service.TranscriptionService.add_job(
                session.id, str(tmp_path / episode), "en", service.JobSource.UI
            )
        with (
            patch.object(batch, "get_settings", return_value=mock_settings),
            patch.object(batch, "notify_batch_completion", new=AsyncMock()),
            patch.dict(batch._batch_metadata, {}, clear=True),
        ):
            await batch.process_batch_session(session.id)
        assert refresher.refresh.await_count == 2
        factory.assert_called_once()
        response = app_client.get(f"/api/batch/session/{session.id}")
        assert response.status_code == 200
        assert [job["media_refresh_results"] for job in response.json()["jobs"]] == [
            {
                "plex": {"status": "queued", "error": None},
                "jellyfin": {"status": "failed", "error": "HTTP 401"},
            },
        ] * 2
        first_job = next(iter(session.jobs.values()))
        response = app_client.get(f"/api/batch/job/{session.id}/{first_job.id}")
        assert (
            response.json()["media_refresh_results"] == first_job.media_refresh_results
        )


class TestJobStatus:
    """Test JobStatus enum."""
    
    def test_status_values(self):
        """Test all job status values exist."""
        from app.transcription_service import JobStatus
        
        assert JobStatus.PENDING == "pending"
        assert JobStatus.EXTRACTING == "extracting"
        assert JobStatus.UPLOADING == "uploading"
        assert JobStatus.TRANSCRIBING == "transcribing"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"


class TestTranscriptionCancelledError:
    """Test TranscriptionCancelledError exception."""
    
    def test_exception_creation(self):
        """Test creating the cancellation exception."""
        from app.transcription_service import TranscriptionCancelledError
        
        error = TranscriptionCancelledError("Job was cancelled")
        assert str(error) == "Job was cancelled"
        assert isinstance(error, Exception)
    
    def test_exception_can_be_raised_and_caught(self):
        """Test that the exception can be raised and caught."""
        from app.transcription_service import TranscriptionCancelledError
        
        with pytest.raises(TranscriptionCancelledError, match="cancelled"):
            raise TranscriptionCancelledError("Operation cancelled")


class TestJobSource:
    """Test JobSource enum."""
    
    def test_source_values(self):
        """Test all job source values exist."""
        from app.transcription_service import JobSource
        
        assert JobSource.UI == "ui"
        assert JobSource.BAZARR == "bazarr"
        assert JobSource.API == "api"
        assert JobSource.WEBHOOK == "webhook"


class TestTranscriptionJob:
    """Test TranscriptionJob dataclass."""
    
    def test_job_creation(self):
        """Test creating a transcription job."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionJob
        
        job = TranscriptionJob(
            id="test-123",
            file_path="/media/video.mkv",
            language="en",
            source=JobSource.UI
        )
        
        assert job.id == "test-123"
        assert job.file_path == "/media/video.mkv"
        assert job.language == "en"
        assert job.source == JobSource.UI
        assert job.status == JobStatus.PENDING
        assert job.progress == 0
        assert job.error is None
    
    def test_get_status_text(self):
        """Test human-readable status text."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionJob
        
        job = TranscriptionJob(
            id="test",
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        job.status = JobStatus.PENDING
        assert job.get_status_text() == "Waiting..."
        
        job.status = JobStatus.EXTRACTING
        assert job.get_status_text() == "Extracting audio"
        
        job.status = JobStatus.UPLOADING
        assert job.get_status_text() == "Uploading to Azure"
        
        job.status = JobStatus.TRANSCRIBING
        assert job.get_status_text() == "Transcribing"
        
        job.status = JobStatus.COMPLETED
        assert job.get_status_text() == "Completed"
        
        job.status = JobStatus.FAILED
        assert job.get_status_text() == "Failed"
        
        job.status = JobStatus.CANCELLED
        assert job.get_status_text() == "Cancelled"
    
    def test_to_dict(self):
        """Test job serialization to dictionary."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionJob
        
        job = TranscriptionJob(
            id="test-123",
            file_path="/media/video.mkv",
            language="en",
            source=JobSource.UI
        )
        
        data = job.to_dict()
        
        assert data['id'] == "test-123"
        assert data['file_path'] == "/media/video.mkv"
        assert data['language'] == "en"
        assert data['source'] == "ui"
        assert data['status'] == "pending"
        assert 'created_at' in data


class TestTranscriptionSession:
    """Test TranscriptionSession dataclass."""
    
    def test_session_creation(self):
        """Test creating a transcription session."""
        from app.transcription_service import JobSource, TranscriptionSession
        
        session = TranscriptionSession(
            id="session-123",
            source=JobSource.UI
        )
        
        assert session.id == "session-123"
        assert session.source == JobSource.UI
        assert len(session.jobs) == 0
        assert len(session.skipped) == 0
    
    def test_session_to_dict(self):
        """Test session serialization to dictionary."""
        from app.transcription_service import (
            JobSource,
            JobStatus,
            TranscriptionJob,
            TranscriptionSession,
        )
        
        session = TranscriptionSession(
            id="session-123",
            source=JobSource.UI
        )
        
        # Add a job
        job = TranscriptionJob(
            id="job-1",
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI,
            status=JobStatus.COMPLETED
        )
        session.jobs["job-1"] = job
        
        data = session.to_dict()
        
        assert data['session_id'] == "session-123"
        assert data['source'] == "ui"
        assert data['total_jobs'] == 1
        assert data['completed'] == 1
        assert data['pending'] == 0
        assert data['failed'] == 0


class TestTranscriptionServiceSessions:
    """Test TranscriptionService session management."""
    
    @pytest.fixture(autouse=True)
    def clear_sessions(self):
        """Clear sessions before and after each test."""
        from app.transcription_service import TranscriptionService
        
        TranscriptionService._sessions.clear()
        yield
        TranscriptionService._sessions.clear()
    
    @pytest.mark.asyncio
    async def test_create_session(self):
        """Test creating a new session."""
        from app.transcription_service import JobSource, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        
        assert session.id is not None
        assert session.source == JobSource.UI
        assert session.id in TranscriptionService._sessions
    
    @pytest.mark.asyncio
    async def test_get_session(self):
        """Test retrieving a session by ID."""
        from app.transcription_service import JobSource, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        
        retrieved = TranscriptionService.get_session(session.id)
        assert retrieved is session
        
        # Non-existent session returns None
        assert TranscriptionService.get_session("nonexistent") is None
    
    @pytest.mark.asyncio
    async def test_get_all_sessions(self):
        """Test getting all sessions."""
        from app.transcription_service import JobSource, TranscriptionService
        
        await TranscriptionService.create_session(source=JobSource.UI)
        await TranscriptionService.create_session(source=JobSource.BAZARR)
        
        sessions = TranscriptionService.get_all_sessions()
        assert len(sessions) == 2


class TestTranscriptionServiceJobs:
    """Test TranscriptionService job management."""
    
    @pytest.fixture(autouse=True)
    def clear_sessions(self):
        """Clear sessions before and after each test."""
        from app.transcription_service import TranscriptionService
        
        TranscriptionService._sessions.clear()
        yield
        TranscriptionService._sessions.clear()
    
    @pytest.mark.asyncio
    async def test_add_job(self):
        """Test adding a job to a session."""
        from app.transcription_service import JobSource, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/media/video.mkv",
            language="en",
            source=JobSource.UI
        )
        
        assert job.id is not None
        assert job.file_path == "/media/video.mkv"
        assert job.id in session.jobs
    
    @pytest.mark.asyncio
    async def test_add_job_invalid_session(self):
        """Test adding job to non-existent session raises error."""
        from app.transcription_service import JobSource, TranscriptionService
        
        with pytest.raises(ValueError, match="Session not found"):
            await TranscriptionService.add_job(
                session_id="nonexistent",
                file_path="/test.mkv",
                language="en",
                source=JobSource.UI
            )
    
    @pytest.mark.asyncio
    async def test_get_job(self):
        """Test retrieving a specific job."""
        from app.transcription_service import JobSource, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        retrieved = TranscriptionService.get_job(session.id, job.id)
        assert retrieved is job
        
        # Non-existent job returns None
        assert TranscriptionService.get_job(session.id, "nonexistent") is None
    
    @pytest.mark.asyncio
    async def test_update_job_status(self):
        """Test updating job status."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        # Patch notify_failure at source to avoid actual notifications
        with patch('app.utils.notification_service.notify_failure', new_callable=AsyncMock):
            await TranscriptionService.update_job_status(
                session.id, job.id, JobStatus.TRANSCRIBING
            )
        
        assert job.status == JobStatus.TRANSCRIBING
        assert job.started_at is not None
    
    @pytest.mark.asyncio
    async def test_update_job_status_completed(self):
        """Test updating job to completed status."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        await TranscriptionService.update_job_status(
            session.id, job.id, JobStatus.COMPLETED,
            segments_count=10,
            duration_seconds=120.5
        )
        
        assert job.status == JobStatus.COMPLETED
        assert job.completed_at is not None
        assert job.segments_count == 10
        assert job.duration_seconds == 120.5
    
    @pytest.mark.asyncio
    async def test_update_job_status_failed(self):
        """Test updating job to failed status."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        # Patch notify_failure at source to avoid actual notifications
        with patch('app.utils.notification_service.notify_failure', new_callable=AsyncMock):
            await TranscriptionService.update_job_status(
                session.id, job.id, JobStatus.FAILED,
                error="Test error message"
            )
        
        assert job.status == JobStatus.FAILED
        assert job.error == "Test error message"
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_cancelled_job_status_cannot_be_overwritten(self):
        """Keep cancellation terminal when a running worker reports progress."""
        from app.transcription_service import (
            JobSource,
            JobStatus,
            TranscriptionService,
        )

        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI,
        )
        await TranscriptionService.update_job_status(
            session.id, job.id, JobStatus.CANCELLED
        )

        await TranscriptionService.update_job_status(
            session.id, job.id, JobStatus.COMPLETED, segments_count=10
        )

        assert job.status == JobStatus.CANCELLED
        assert job.segments_count == 0


class TestTranscriptionServiceActiveJobs:
    """Test active job tracking."""
    
    @pytest.fixture(autouse=True)
    def clear_sessions(self):
        """Clear sessions before and after each test."""
        from app.transcription_service import TranscriptionService
        
        TranscriptionService._sessions.clear()
        yield
        TranscriptionService._sessions.clear()
    
    @pytest.mark.asyncio
    async def test_get_active_jobs(self):
        """Test getting active jobs across all sessions."""
        from app.transcription_service import JobSource, JobStatus, TranscriptionService
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        
        # Add jobs with different statuses
        job1 = await TranscriptionService.add_job(session.id, "/f1.mkv", "en", JobSource.UI)
        job2 = await TranscriptionService.add_job(session.id, "/f2.mkv", "en", JobSource.UI)
        job3 = await TranscriptionService.add_job(session.id, "/f3.mkv", "en", JobSource.UI)
        
        job1.status = JobStatus.PENDING
        job2.status = JobStatus.TRANSCRIBING
        job3.status = JobStatus.COMPLETED
        
        active = TranscriptionService.get_active_jobs()
        
        # Only transcribing job should be active
        assert len(active) == 1
        assert active[0].id == job2.id


class TestTranscriptionServiceHelpers:
    """Test helper methods in TranscriptionService."""
    
    def test_get_azure_locale(self):
        """Test Azure locale conversion."""
        from app.transcription_service import TranscriptionService

        # Access private method for testing
        locale = TranscriptionService._get_azure_locale("en")
        assert locale == "en-US"
        
        locale = TranscriptionService._get_azure_locale("de")
        assert locale == "de-DE"
        
        locale = TranscriptionService._get_azure_locale("fr")
        assert locale == "fr-FR"

    def test_get_azure_locale_preserves_explicit_locale(self):
        """Test that explicit locale values are not remapped."""
        from app.transcription_service import TranscriptionService

        locale = TranscriptionService._get_azure_locale("en-AU")
        assert locale == "en-AU"


class TestTranscriptionServiceConcurrency:
    """Test global transcription concurrency control with PrioritySemaphore."""

    @pytest.fixture(autouse=True)
    def reset_pipeline_semaphore(self):
        """Reset pipeline semaphore before and after each test."""
        from app.transcription_service import TranscriptionService

        TranscriptionService._pipeline_semaphore = None
        yield
        TranscriptionService._pipeline_semaphore = None

    def test_semaphore_lazy_initialization(self):
        """Pipeline semaphore is None until first access, then stable."""
        from app.transcription_service import TranscriptionService

        assert TranscriptionService._pipeline_semaphore is None

        sem = TranscriptionService._get_pipeline_semaphore()
        assert sem is not None
        assert TranscriptionService._pipeline_semaphore is sem

        sem2 = TranscriptionService._get_pipeline_semaphore()
        assert sem2 is sem

    @pytest.mark.asyncio
    async def test_acquire_and_release_slot(self):
        """Basic acquire/release completes without blocking."""
        from app.transcription_service import TranscriptionService

        sem = TranscriptionService._get_pipeline_semaphore()
        await sem.acquire(priority=1)
        sem.release()

    @pytest.mark.asyncio
    async def test_priority_acquire(self):
        """Bazarr priority acquire (priority=0) completes without blocking."""
        from app.transcription_service import TranscriptionService

        sem = TranscriptionService._get_pipeline_semaphore()
        await sem.acquire(priority=0)
        sem.release()

    @pytest.mark.asyncio
    async def test_multiple_slots(self):
        """Multiple slots can be acquired and released up to the limit."""
        from app.transcription_service import TranscriptionService

        sem = TranscriptionService._get_pipeline_semaphore()
        for _ in range(3):
            await sem.acquire(priority=1)
        for _ in range(3):
            sem.release()

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """High-priority (Bazarr) waiter is served before normal waiter."""
        import asyncio

        from app.transcription_service import PrioritySemaphore

        sem = PrioritySemaphore(1)
        # Fill the single slot
        await sem.acquire(priority=1)

        served: list[str] = []

        async def bazarr_job():
            await sem.acquire(priority=0)
            served.append("bazarr")
            sem.release()

        async def batch_job():
            await sem.acquire(priority=1)
            served.append("batch")
            sem.release()

        # Enqueue batch first, then Bazarr — Bazarr should still win
        t_batch = asyncio.create_task(batch_job())
        await asyncio.sleep(0)  # let batch park in queue
        t_bazarr = asyncio.create_task(bazarr_job())
        await asyncio.sleep(0)  # let bazarr park in queue

        sem.release()  # release the initial slot; Bazarr should be served first
        await asyncio.gather(t_batch, t_bazarr)

        assert served[0] == "bazarr", f"Expected bazarr first, got: {served}"

    @pytest.mark.asyncio
    async def test_cancelled_waiter_does_not_consume_a_released_slot(self):
        """Cancelled jobs leave the pipeline semaphore usable by later work."""
        from app.transcription_service import PrioritySemaphore

        sem = PrioritySemaphore(1)
        await sem.acquire()

        async def wait_for_slot():
            await sem.acquire()

        cancelled_waiter = asyncio.create_task(wait_for_slot())
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        sem.release()
        await sem.acquire()
        sem.release()

        assert sem._value == 1


class TestAudioTranscriptionLifecycle:
    """Exercise ASR cleanup and candidate forwarding through the public service."""

    @pytest.fixture
    def audio_pipeline(
        self,
        patched_settings: MagicMock,
        mock_transcription_result: TranscriptionResult,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> Iterator[MagicMock]:
        """Mock subprocess and Azure boundaries while retaining real file cleanup."""
        patched_settings.concurrent_transcriptions = 1
        monkeypatch.setattr(TranscriptionService, "_sessions", {})
        monkeypatch.setattr(TranscriptionService, "_pipeline_semaphore", None)
        monkeypatch.setattr(
            TranscriptionService, "_azure_semaphore", asyncio.Semaphore(1)
        )
        transcriber = MagicMock()
        transcriber.directories = []
        transcriber.audio_inputs = []
        transcriber.upload_audio = AsyncMock(
            return_value=("https://example.com/audio", "test-blob")
        )
        transcriber.create_transcription = AsyncMock(
            return_value=MagicMock(id="azure-job", locale="en-US")
        )
        transcriber.get_transcription_status = AsyncMock(
            return_value=MagicMock(status=MagicMock(value="Succeeded"), locale="en-US")
        )
        transcriber.get_transcription_result = AsyncMock(
            return_value=mock_transcription_result
        )
        transcriber.delete_transcription = AsyncMock(return_value=True)
        transcriber.delete_blob = AsyncMock(return_value=True)
        transcriber.close = AsyncMock()

        def make_dir(prefix: str) -> str:
            directory = tmp_path / f"{prefix}{len(transcriber.directories)}"
            directory.mkdir()
            transcriber.directories.append(directory)
            return str(directory)

        async def spawn(*command: str, **kwargs: object) -> MagicMock:
            transcriber.audio_inputs.append(
                await asyncio.to_thread(Path(command[3]).read_bytes)
            )

            async def communicate() -> tuple[bytes, bytes]:
                await asyncio.to_thread(Path(command[-1]).write_bytes, b"ogg")
                return b"", b""

            process = MagicMock(returncode=0)
            process.communicate = AsyncMock(side_effect=communicate)
            return process

        with (
            patch(
                "app.transcription_service.get_settings", return_value=patched_settings
            ),
            patch("app.transcription_service.make_temp_dir", side_effect=make_dir),
            patch(
                "app.transcription_service.AzureBatchTranscriber",
                return_value=transcriber,
            ),
            patch(
                "app.transcription_service.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                side_effect=spawn,
            ),
        ):
            yield transcriber

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw_pcm", [False, True])
    @pytest.mark.parametrize("candidates", [None, ["en-US", "nl-NL"]])
    async def test_should_transcribe_and_clean_up_audio_data(
        self,
        raw_pcm: bool,
        candidates: list[str] | None,
        audio_pipeline: MagicMock,
        mock_transcription_result: TranscriptionResult,
    ) -> None:
        """Normal and language-identification jobs retain the same ASR lifecycle."""
        audio = b"\x00\x01" * 16
        result, job = await TranscriptionService.transcribe_audio_data(
            audio,
            "en",
            JobSource.BAZARR,
            file_name="audio.wav",
            is_raw_pcm=raw_pcm,
            candidate_locales=candidates,
        )
        assert result == mock_transcription_result
        assert job.status == JobStatus.COMPLETED
        if raw_pcm:
            with wave.open(io.BytesIO(audio_pipeline.audio_inputs[0]), "rb") as wav:
                assert (
                    wav.getnchannels(),
                    wav.getsampwidth(),
                    wav.getframerate(),
                    wav.readframes(wav.getnframes()),
                ) == (1, 2, 16000, audio)
        else:
            assert audio_pipeline.audio_inputs[0] == audio
        audio_pipeline.create_transcription.assert_awaited_once_with(
            audio_url="https://example.com/audio",
            locale="en-US",
            display_name="bazarr-audio",
            candidate_locales=candidates,
        )
        audio_pipeline.delete_transcription.assert_awaited_once_with("azure-job")
        audio_pipeline.delete_blob.assert_awaited_once_with("test-blob")
        audio_pipeline.close.assert_awaited_once_with()
        assert not await asyncio.to_thread(audio_pipeline.directories[0].exists)

    @pytest.mark.asyncio
    async def test_should_cancel_queued_job_without_leaking_directory_or_slot(
        self, audio_pipeline: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A disconnected queued ASR job is cancelled and never releases an unowned slot."""
        semaphore = PrioritySemaphore(1)
        await semaphore.acquire()
        monkeypatch.setattr(TranscriptionService, "_pipeline_semaphore", semaphore)
        queued = asyncio.Event()
        add_job = TranscriptionService.add_job

        async def record_job(
            session_id: str, file_path: str, language: str, source: JobSource
        ) -> TranscriptionJob:
            job = await add_job(session_id, file_path, language, source)
            queued.set()
            return job

        with patch.object(TranscriptionService, "add_job", side_effect=record_job):
            task = asyncio.create_task(
                TranscriptionService.transcribe_audio_data(
                    b"\x00\x01", "en", JobSource.BAZARR
                )
            )
            await asyncio.wait_for(queued.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        session = next(iter(TranscriptionService.get_all_sessions().values()))
        assert next(iter(session.jobs.values())).status == JobStatus.CANCELLED
        assert not await asyncio.to_thread(audio_pipeline.directories[0].exists)
        audio_pipeline.upload_audio.assert_not_awaited()
        semaphore.release()
        _, subsequent_job = await asyncio.wait_for(
            TranscriptionService.transcribe_audio_data(
                b"\x00\x01", "en", JobSource.BAZARR
            ),
            timeout=2,
        )
        assert subsequent_job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cleanup_method", ["delete_transcription", "delete_blob"])
    async def test_should_release_capacity_when_cleanup_is_cancelled(
        self, cleanup_method: str, audio_pipeline: MagicMock
    ) -> None:
        """Cancellation during cleanup cannot leak the Azure quota slot or client."""
        cleaning = asyncio.Event()

        async def pending_cleanup(resource_id: str) -> None:
            cleaning.set()
            await asyncio.Event().wait()

        cleanup = getattr(audio_pipeline, cleanup_method)
        cleanup.side_effect = pending_cleanup
        task = asyncio.create_task(
            TranscriptionService.transcribe_audio_data(
                b"\x00\x01", "en", JobSource.BAZARR
            )
        )
        await asyncio.wait_for(cleaning.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        session = next(iter(TranscriptionService.get_all_sessions().values()))
        assert next(iter(session.jobs.values())).status == JobStatus.COMPLETED
        audio_pipeline.close.assert_awaited_once_with()
        assert not await asyncio.to_thread(audio_pipeline.directories[0].exists)
        cleanup.side_effect = None
        _, subsequent_job = await asyncio.wait_for(
            TranscriptionService.transcribe_audio_data(
                b"\x00\x01", "en", JobSource.BAZARR
            ),
            timeout=2,
        )
        assert subsequent_job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_should_kill_conversion_when_request_is_cancelled(
        self, audio_pipeline: MagicMock
    ) -> None:
        """Cancelled compression stops FFmpeg before deleting temporary audio."""
        converting = asyncio.Event()
        process = MagicMock(returncode=None)

        async def communicate() -> None:
            converting.set()
            await asyncio.Event().wait()

        process.communicate = AsyncMock(side_effect=communicate)
        process.wait = AsyncMock(return_value=-9)
        with patch(
            "app.transcription_service.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ):
            task = asyncio.create_task(
                TranscriptionService.transcribe_audio_data(
                    b"\x00\x01", "en", JobSource.BAZARR
                )
            )
            await asyncio.wait_for(converting.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once_with()
        process.wait.assert_awaited_once_with()
        audio_pipeline.upload_audio.assert_not_awaited()
        assert not await asyncio.to_thread(audio_pipeline.directories[0].exists)

    @pytest.mark.asyncio
    async def test_should_clean_azure_resources_when_pending_request_is_cancelled(
        self, audio_pipeline: MagicMock
    ) -> None:
        """Cancellation during polling deletes known Azure resources and preserves capacity."""
        polling = asyncio.Event()

        async def pending_status(job_id: str) -> None:
            polling.set()
            await asyncio.Event().wait()

        audio_pipeline.get_transcription_status.side_effect = pending_status
        task = asyncio.create_task(
            TranscriptionService.transcribe_audio_data(
                b"\x00\x01", "en", JobSource.BAZARR
            )
        )
        await asyncio.wait_for(polling.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        session = next(iter(TranscriptionService.get_all_sessions().values()))
        assert next(iter(session.jobs.values())).status == JobStatus.CANCELLED
        audio_pipeline.delete_transcription.assert_awaited_once_with("azure-job")
        audio_pipeline.delete_blob.assert_awaited_once_with("test-blob")
        audio_pipeline.close.assert_awaited_once_with()
        assert not await asyncio.to_thread(audio_pipeline.directories[0].exists)
        audio_pipeline.get_transcription_status.side_effect = None
        _, subsequent_job = await asyncio.wait_for(
            TranscriptionService.transcribe_audio_data(
                b"\x00\x01", "en", JobSource.BAZARR
            ),
            timeout=2,
        )
        assert subsequent_job.status == JobStatus.COMPLETED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
