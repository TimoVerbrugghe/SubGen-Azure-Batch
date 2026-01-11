"""
Tests for transcription service module.

Tests cover:
- TranscriptionJob dataclass
- TranscriptionSession management
- Session/Job lifecycle
- Status updates and tracking
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionJob)
        
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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionJob)
        
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
    
    def test_to_dict(self):
        """Test job serialization to dictionary."""
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionJob)
        
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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionJob,
                                               TranscriptionSession)
        
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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionService)
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        # Patch notify_failure at source to avoid actual notifications
        with patch('app.notification_service.notify_failure', new_callable=AsyncMock):
            await TranscriptionService.update_job_status(
                session.id, job.id, JobStatus.TRANSCRIBING
            )
        
        assert job.status == JobStatus.TRANSCRIBING
        assert job.started_at is not None
    
    @pytest.mark.asyncio
    async def test_update_job_status_completed(self):
        """Test updating job to completed status."""
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionService)
        
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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionService)
        
        session = await TranscriptionService.create_session(source=JobSource.UI)
        job = await TranscriptionService.add_job(
            session_id=session.id,
            file_path="/test.mkv",
            language="en",
            source=JobSource.UI
        )
        
        # Patch notify_failure at source to avoid actual notifications
        with patch('app.notification_service.notify_failure', new_callable=AsyncMock):
            await TranscriptionService.update_job_status(
                session.id, job.id, JobStatus.FAILED,
                error="Test error message"
            )
        
        assert job.status == JobStatus.FAILED
        assert job.error == "Test error message"
        assert job.completed_at is not None


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
        from app.transcription_service import (JobSource, JobStatus,
                                               TranscriptionService)
        
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
