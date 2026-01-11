"""
Transcription Service - Unified transcription logic for all sources.

This module provides a centralized transcription service that can be used by:
- Batch processing from the Web UI
- Bazarr ASR endpoint
- Direct API calls

All transcription jobs are tracked in a unified session system for visibility.
"""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from app.audio_extractor import extract_audio, make_temp_dir
from app.azure_batch_transcriber import (AzureBatchTranscriber,
                                         TranscriptionResult)
from app.config import format_duration, get_settings
from app.language_code import LanguageCode

logger = logging.getLogger(__name__)


class TranscriptionCancelledError(Exception):
    """Raised when a transcription job is cancelled."""
    pass


class JobStatus(str, Enum):
    """Transcription job status."""
    PENDING = "pending"
    EXTRACTING = "extracting"
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSource(str, Enum):
    """Source of the transcription job."""
    UI = "ui"
    BAZARR = "bazarr"
    API = "api"
    WEBHOOK = "webhook"


@dataclass
class TranscriptionJob:
    """Represents a single transcription job."""
    id: str
    file_path: str  # For UI jobs: video path, for Bazarr: video_file param or "unknown"
    language: str
    source: JobSource
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    error: Optional[str] = None
    srt_path: Optional[str] = None
    azure_job_id: Optional[str] = None
    blob_name: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    segments_count: int = 0
    duration_seconds: float = 0.0
    # Media server refresh tracking
    media_refresh_status: Optional[Dict[str, bool]] = None  # e.g., {"plex": True, "jellyfin": False}
    
    def get_status_text(self) -> str:
        """Get human-readable status text."""
        status_map = {
            JobStatus.PENDING: "Waiting...",
            JobStatus.EXTRACTING: "Extracting audio",
            JobStatus.UPLOADING: "Uploading to Azure",
            JobStatus.TRANSCRIBING: "Transcribing",
            JobStatus.COMPLETED: "Completed",
            JobStatus.FAILED: "Failed",
            JobStatus.CANCELLED: "Cancelled",
        }
        return status_map.get(self.status, "")
    
    def to_dict(self) -> dict:
        """Convert job to dictionary for API responses."""
        return {
            "id": self.id,
            "file_path": self.file_path,
            "language": self.language,
            "source": self.source.value,
            "status": self.status.value,
            "status_text": self.get_status_text(),
            "progress": self.progress,
            "error": self.error,
            "srt_path": self.srt_path,
            "azure_job_id": self.azure_job_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "segments_count": self.segments_count,
            "duration_seconds": self.duration_seconds,
            "media_refresh_status": self.media_refresh_status,
        }


@dataclass
class TranscriptionSession:
    """A session containing one or more transcription jobs."""
    id: str
    jobs: Dict[str, TranscriptionJob] = field(default_factory=dict)
    skipped: List[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    source: JobSource = JobSource.UI
    notify_bazarr: bool = True
    
    def to_dict(self) -> dict:
        """Convert session to dictionary for API responses."""
        completed = sum(1 for j in self.jobs.values() if j.status == JobStatus.COMPLETED)
        failed = sum(1 for j in self.jobs.values() if j.status == JobStatus.FAILED)
        in_progress = sum(1 for j in self.jobs.values() if j.status in (JobStatus.EXTRACTING, JobStatus.UPLOADING, JobStatus.TRANSCRIBING))
        pending = sum(1 for j in self.jobs.values() if j.status == JobStatus.PENDING)
        
        return {
            "session_id": self.id,
            "source": self.source.value,
            "total_jobs": len(self.jobs),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
            "failed": failed,
            "jobs": [j.to_dict() for j in self.jobs.values()],
            "skipped": self.skipped,
            "created_at": self.created_at.isoformat(),
        }


class TranscriptionService:
    """
    Centralized service for all transcription operations.
    
    Provides unified job tracking, audio processing, and Azure integration
    for all sources (UI, Bazarr, API).
    """
    
    # Class-level storage for sessions (would use Redis/DB in production)
    _sessions: Dict[str, TranscriptionSession] = {}
    _lock = asyncio.Lock()
    
    @classmethod
    def get_all_sessions(cls) -> Dict[str, TranscriptionSession]:
        """Get all active sessions."""
        return cls._sessions
    
    @classmethod
    def get_session(cls, session_id: str) -> Optional[TranscriptionSession]:
        """Get a specific session by ID."""
        return cls._sessions.get(session_id)
    
    @classmethod
    def get_job(cls, session_id: str, job_id: str) -> Optional[TranscriptionJob]:
        """Get a specific job by session and job ID."""
        session = cls._sessions.get(session_id)
        if session:
            return session.jobs.get(job_id)
        return None
    
    @classmethod
    async def create_session(
        cls,
        source: JobSource = JobSource.UI,
        notify_bazarr: bool = True
    ) -> TranscriptionSession:
        """Create a new transcription session."""
        async with cls._lock:
            session_id = str(uuid.uuid4())[:8]
            session = TranscriptionSession(
                id=session_id,
                source=source,
                notify_bazarr=notify_bazarr,
            )
            cls._sessions[session_id] = session
            logger.info(f"Created transcription session: {session_id} (source: {source.value})")
            return session
    
    @classmethod
    async def add_job(
        cls,
        session_id: str,
        file_path: str,
        language: str,
        source: JobSource,
    ) -> TranscriptionJob:
        """Add a job to a session."""
        async with cls._lock:
            session = cls._sessions.get(session_id)
            if not session:
                raise ValueError(f"Session not found: {session_id}")
            
            job_id = str(uuid.uuid4())[:8]
            job = TranscriptionJob(
                id=job_id,
                file_path=file_path,
                language=language,
                source=source,
            )
            session.jobs[job_id] = job
            logger.debug(f"Added job {job_id} to session {session_id}")
            return job
    
    @classmethod
    async def update_job_status(
        cls,
        session_id: str,
        job_id: str,
        status: JobStatus,
        **kwargs
    ):
        """Update job status and optional fields."""
        job = cls.get_job(session_id, job_id)
        if job:
            job.status = status
            for key, value in kwargs.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            
            # Log status changes
            if status == JobStatus.TRANSCRIBING:
                job.started_at = datetime.now()
                logger.info(f"[{job_id}] Transcribing: {job.file_path}")
            elif status == JobStatus.COMPLETED:
                job.completed_at = datetime.now()
                duration = (job.completed_at - job.created_at).total_seconds()
                duration_str = format_duration(duration)
                logger.info(
                    f"[{job_id}] Completed '{Path(job.file_path).name}' in {duration_str}, "
                    f"{job.segments_count} segments"
                )
            elif status == JobStatus.FAILED:
                job.completed_at = datetime.now()
                logger.error(f"[{job_id}] Failed: {job.file_path} - {job.error}")
                
                # Send failure notification (fire-and-forget, non-blocking)
                from app.notification_service import notify_failure
                asyncio.create_task(
                    notify_failure(
                        file_path=job.file_path,
                        error=job.error or "Unknown error",
                        job_id=job_id,
                        source=job.source.value if job.source else None,
                    )
                )
    
    @classmethod
    def get_active_jobs(cls) -> List[TranscriptionJob]:
        """Get all currently active (in-progress) jobs across all sessions."""
        active = []
        for session in cls._sessions.values():
            for job in session.jobs.values():
                if job.status in (JobStatus.EXTRACTING, JobStatus.UPLOADING, JobStatus.TRANSCRIBING):
                    active.append(job)
        return active
    
    @classmethod
    async def transcribe_audio_data(
        cls,
        audio_data: bytes,
        language: str,
        source: JobSource,
        file_name: str = "unknown",
        is_raw_pcm: bool = False,
        on_status_change: Optional[Callable] = None,
    ) -> Tuple[TranscriptionResult, TranscriptionJob]:
        """
        Transcribe audio data (bytes) - used by Bazarr ASR endpoint.
        
        This method:
        1. Creates a session and job for tracking
        2. Converts raw PCM to WAV if needed
        3. Converts to OGG/Opus for efficient upload
        4. Uploads to Azure and transcribes
        5. Cleans up resources
        
        Args:
            audio_data: Raw audio bytes (WAV or raw PCM).
            language: Language code (e.g., 'en', 'de').
            source: Source of the request.
            file_name: Original file name for logging.
            is_raw_pcm: If True, audio_data is raw PCM (16-bit, 16kHz, mono).
            on_status_change: Optional callback for status updates.
            
        Returns:
            Tuple of (TranscriptionResult, TranscriptionJob).
        """
        settings = get_settings()
        temp_dir = make_temp_dir(prefix="subgen_transcribe_")
        
        # Create session and job for tracking
        session = await cls.create_session(source=source, notify_bazarr=False)
        job = await cls.add_job(session.id, file_name, language, source)
        
        try:
            # Update status
            await cls.update_job_status(session.id, job.id, JobStatus.EXTRACTING)
            
            # Save audio data to temp file
            if is_raw_pcm:
                # Wrap raw PCM in WAV container
                import wave
                wav_path = os.path.join(temp_dir, "audio.wav")
                with wave.open(wav_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)  # mono
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(16000)  # 16kHz
                    wav_file.writeframes(audio_data)
                temp_audio = wav_path
                logger.debug(f"[{job.id}] Created WAV from raw PCM: {len(audio_data)} bytes")
            else:
                # Save as-is (already in a container format)
                temp_audio = os.path.join(temp_dir, "audio.wav")
                with open(temp_audio, 'wb') as f:
                    f.write(audio_data)
            
            # Convert to OGG/Opus for smaller upload size
            ogg_path = os.path.join(temp_dir, "audio.ogg")
            await cls._convert_to_ogg(temp_audio, ogg_path)
            
            original_size = len(audio_data)
            compressed_size = os.path.getsize(ogg_path)
            logger.info(f"[{job.id}] Audio compressed: {original_size:,} → {compressed_size:,} bytes ({100*compressed_size/original_size:.1f}%)")
            
            # Convert language to Azure locale
            azure_locale = cls._get_azure_locale(language)
            
            # Upload and transcribe
            await cls.update_job_status(session.id, job.id, JobStatus.UPLOADING)
            
            transcriber = AzureBatchTranscriber()
            try:
                # Upload to Azure
                audio_url, blob_name = await transcriber.upload_audio(ogg_path)
                job.blob_name = blob_name
                logger.info(f"[Session {session.id}] [{job.id}] Uploaded to Azure: {blob_name}")
                
                # Create transcription job
                await cls.update_job_status(session.id, job.id, JobStatus.TRANSCRIBING)
                
                azure_job = await transcriber.create_transcription(
                    audio_url=audio_url,
                    locale=azure_locale,
                    display_name=f"{source.value}-{Path(file_name).stem if file_name != 'unknown' else job.id}"
                )
                job.azure_job_id = azure_job.id
                logger.info(f"[Session {session.id}] [{job.id}] Created Azure transcription: {azure_job.id}")
                
                # Wait for completion with periodic logging
                result = await cls._wait_for_transcription_with_logging(
                    transcriber, azure_job.id, job
                )
                
                # Update job with results
                await cls.update_job_status(
                    session.id, job.id, JobStatus.COMPLETED,
                    segments_count=len(result.segments),
                    duration_seconds=result.duration,
                )
                
                return result, job
                
            finally:
                # Cleanup Azure resources
                if job.blob_name:
                    try:
                        await transcriber.delete_blob(job.blob_name)
                        logger.info(f"[Session {session.id}] [{job.id}] Deleted Azure blob: {job.blob_name}")
                    except Exception as e:
                        logger.warning(f"[Session {session.id}] [{job.id}] Failed to delete blob: {e}")
                
                if job.azure_job_id:
                    try:
                        await transcriber.delete_transcription(job.azure_job_id)
                        logger.info(f"[Session {session.id}] [{job.id}] Deleted Azure transcription: {job.azure_job_id}")
                        logger.debug(f"[{job.id}] Deleted Azure job: {job.azure_job_id}")
                    except Exception as e:
                        logger.warning(f"[{job.id}] Failed to delete Azure job: {e}")
                
                await transcriber.close()
                
        except Exception as e:
            await cls.update_job_status(
                session.id, job.id, JobStatus.FAILED,
                error=str(e)
            )
            raise
            
        finally:
            # Cleanup temp files
            try:
                import shutil
                shutil.rmtree(temp_dir)
            except Exception:
                pass
    
    @classmethod
    async def transcribe_file(
        cls,
        file_path: str,
        language: str,
        source: JobSource = JobSource.UI,
        session_id: Optional[str] = None,
        job_id: Optional[str] = None,
        save_srt: bool = True,
        refresh_media_servers: bool = True,
    ) -> Tuple[Optional[TranscriptionResult], TranscriptionJob]:
        """
        Transcribe a video/audio file - used by batch UI.
        
        Args:
            file_path: Path to video/audio file.
            language: Language code.
            source: Source of the request.
            session_id: Optional existing session ID.
            job_id: Optional existing job ID (if already added to session).
            save_srt: Whether to save SRT file next to video.
            refresh_media_servers: Whether to refresh metadata on configured media servers.
            
        Returns:
            Tuple of (TranscriptionResult, TranscriptionJob).
        """
        settings = get_settings()
        
        # Create or get session
        if session_id:
            session = cls.get_session(session_id)
            if not session:
                session = await cls.create_session(source=source)
        else:
            session = await cls.create_session(source=source)
        
        # Use existing job or create new one
        if job_id and session_id:
            job = session.jobs.get(job_id)
            if not job:
                # Job not found, create new one
                job = await cls.add_job(session.id, file_path, language, source)
        else:
            # Add new job
            job = await cls.add_job(session.id, file_path, language, source)
        
        try:
            # Extract audio
            await cls.update_job_status(session.id, job.id, JobStatus.EXTRACTING)
            audio_path = await extract_audio(file_path, output_format='ogg')
            logger.info(f"[{job.id}] Extracted audio: {audio_path}")
            
            # Convert language
            azure_locale = cls._get_azure_locale(language)
            
            # Check if cancelled before upload
            if job.status == JobStatus.CANCELLED:
                logger.info(f"[{job.id}] Job cancelled before upload")
                raise TranscriptionCancelledError("Cancelled before upload")
            
            # Upload and transcribe
            await cls.update_job_status(session.id, job.id, JobStatus.UPLOADING)
            
            transcriber = AzureBatchTranscriber()
            try:
                audio_url, blob_name = await transcriber.upload_audio(audio_path)
                job.blob_name = blob_name
                logger.info(f"[{job.id}] Uploaded: {blob_name}")
                
                # Check if cancelled before starting transcription
                if job.status == JobStatus.CANCELLED:
                    logger.info(f"[{job.id}] Job cancelled before transcription")
                    raise TranscriptionCancelledError("Cancelled before transcription")
                
                await cls.update_job_status(session.id, job.id, JobStatus.TRANSCRIBING)
                
                azure_job = await transcriber.create_transcription(
                    audio_url=audio_url,
                    locale=azure_locale,
                    display_name=f"batch-{Path(file_path).stem}"
                )
                job.azure_job_id = azure_job.id
                logger.info(f"[{job.id}] Created Azure job: {azure_job.id}")
                
                result = await cls._wait_for_transcription_with_logging(
                    transcriber, azure_job.id, job
                )
                
                # Generate SRT content
                srt_content = result.to_srt()
                
                # Append credit line if configured (APPEND)
                if settings.transcription.append_credit_line:
                    from app.subtitle_utils import append_credit_line
                    srt_content = append_credit_line(srt_content)
                    logger.debug(f"[{job.id}] Appended credit line")
                
                # Save subtitle file if requested
                output_path = None
                if save_srt:
                    from app.audio_extractor import is_audio_file
                    from app.subtitle_utils import save_lrc
                    from app.subtitle_utils import save_srt as save_srt_file

                    # Check if this is an audio file and LRC is enabled
                    if is_audio_file(file_path) and settings.transcription.lrc_for_audio_files:
                        output_path = save_lrc(srt_content, file_path, language)
                        logger.info(f"[Session {session.id}] [{job.id}] Saved LRC: {output_path}")
                    else:
                        output_path = save_srt_file(srt_content, file_path, language)
                        logger.info(f"[Session {session.id}] [{job.id}] Saved SRT: {output_path}")
                    
                    job.srt_path = output_path
                
                # Refresh media servers so they pick up the new subtitle
                refresh_results = {}
                if refresh_media_servers and output_path:
                    from app.media_server_client import refresh_by_file_path
                    try:
                        refresh_results = await refresh_by_file_path(file_path)
                        refreshed = [k for k, v in refresh_results.items() if v]
                        if refreshed:
                            logger.info(f"[Session {session.id}] [{job.id}] Refreshed metadata on: {', '.join(refreshed)}")
                    except Exception as e:
                        logger.warning(f"[Session {session.id}] [{job.id}] Media server refresh failed: {e}")
                
                await cls.update_job_status(
                    session.id, job.id, JobStatus.COMPLETED,
                    segments_count=len(result.segments),
                    srt_path=output_path,
                    media_refresh_status=refresh_results if refresh_results else None,
                )
                
                # Cleanup Azure job
                await transcriber.delete_transcription(azure_job.id)
                
                return result, job
                
            finally:
                if job.blob_name:
                    try:
                        await transcriber.delete_blob(job.blob_name)
                    except Exception as e:
                        logger.warning(f"[{job.id}] Failed to delete blob: {e}")
                
                await transcriber.close()
                
                # Cleanup audio file
                try:
                    Path(audio_path).unlink()
                except Exception:
                    pass
        
        except TranscriptionCancelledError:
            # Job was cancelled - don't mark as failed, just exit silently
            logger.info(f"[{job.id}] Transcription cancelled, cleanup complete")
            return None, job
                    
        except Exception as e:
            await cls.update_job_status(
                session.id, job.id, JobStatus.FAILED,
                error=str(e)
            )
            raise
    
    @classmethod
    async def _wait_for_transcription_with_logging(
        cls,
        transcriber: AzureBatchTranscriber,
        azure_job_id: str,
        job: TranscriptionJob,
    ) -> TranscriptionResult:
        """Wait for transcription with periodic logging."""
        settings = get_settings()
        poll_count = 0
        max_polls = 360  # 1 hour at 10s intervals
        last_status = None
        last_log_time = time.time()
        
        while poll_count < max_polls:
            # Check if job was cancelled
            if job.status == JobStatus.CANCELLED:
                logger.info(f"[{job.id}] Job was cancelled, stopping poll loop")
                raise TranscriptionCancelledError("Transcription was cancelled")
            
            azure_job = await transcriber.get_transcription_status(azure_job_id)
            
            # Log status changes
            if azure_job.status.value != last_status:
                logger.info(f"[{job.id}] Azure status: {azure_job.status.value}")
                last_status = azure_job.status.value
            
            if azure_job.status.value == "Succeeded":
                break
            elif azure_job.status.value == "Failed":
                raise Exception(azure_job.error_message or "Transcription failed")
            
            # Log progress periodically
            current_time = time.time()
            if current_time - last_log_time >= 30:
                logger.info(f"[{job.id}] Transcribing... (poll {poll_count}/{max_polls})")
                last_log_time = current_time
            
            await asyncio.sleep(settings.job_poll_interval)
            poll_count += 1
        
        if poll_count >= max_polls:
            raise Exception("Transcription timed out")
        
        return await transcriber.get_transcription_result(azure_job_id)
    
    @classmethod
    async def _convert_to_ogg(cls, input_path: str, output_path: str):
        """Convert audio to OGG/Opus format for efficient upload."""
        import subprocess
        
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vn',  # No video
            '-acodec', 'libopus',
            '-ar', '16000',
            '-ac', '1',
            '-b:a', '64k',
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg conversion failed: {stderr.decode()}")
    
    @classmethod
    def _get_azure_locale(cls, language: str) -> str:
        """Convert language code to Azure locale."""
        # First try LanguageCode enum
        lang_code = LanguageCode.from_string(language)
        if lang_code != LanguageCode.NONE:
            return lang_code.to_azure_locale()
        
        # Check if already a locale
        if '-' in language:
            return language
        
        # Map simple codes to locales
        default_regions = {
            'en': 'en-US', 'de': 'de-DE', 'fr': 'fr-FR', 'es': 'es-ES',
            'it': 'it-IT', 'pt': 'pt-BR', 'nl': 'nl-NL', 'ja': 'ja-JP',
            'ko': 'ko-KR', 'zh': 'zh-CN', 'ru': 'ru-RU', 'ar': 'ar-SA',
            'hi': 'hi-IN', 'tr': 'tr-TR', 'pl': 'pl-PL', 'cs': 'cs-CZ',
            'da': 'da-DK', 'fi': 'fi-FI', 'el': 'el-GR', 'he': 'he-IL',
            'hu': 'hu-HU', 'id': 'id-ID', 'no': 'nb-NO', 'ro': 'ro-RO',
            'sk': 'sk-SK', 'sv': 'sv-SE', 'th': 'th-TH', 'uk': 'uk-UA',
            'vi': 'vi-VN',
        }
        
        lang_lower = language.lower()
        return default_regions.get(lang_lower, f"{lang_lower}-{lang_lower.upper()}")
    
    @classmethod
    async def cancel_session(cls, session_id: str) -> dict:
        """
        Cancel a session: mark pending/in-progress jobs as cancelled and cleanup Azure resources.
        
        Args:
            session_id: Session ID to cancel.
            
        Returns:
            Dict with cancellation results: {cancelled: int, cleaned_blobs: int, errors: list}
        """
        session = cls._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        cancelled_count = 0
        cleaned_blobs = 0
        errors = []
        
        transcriber = None
        
        try:
            # Use default constructor - reads settings automatically
            transcriber = AzureBatchTranscriber()
            
            for job in session.jobs.values():
                # Only cancel jobs that aren't already completed or failed
                if job.status in (JobStatus.PENDING, JobStatus.EXTRACTING, 
                                  JobStatus.UPLOADING, JobStatus.TRANSCRIBING):
                    job.status = JobStatus.CANCELLED
                    job.completed_at = datetime.now()
                    cancelled_count += 1
                    logger.info(f"[Session {session_id}] [{job.id}] Cancelled job")
                    
                    # Try to cleanup Azure blob if uploaded
                    if job.blob_name:
                        try:
                            await transcriber.delete_blob(job.blob_name)
                            cleaned_blobs += 1
                            logger.info(f"[Session {session_id}] [{job.id}] Deleted blob: {job.blob_name}")
                        except Exception as e:
                            errors.append(f"Failed to delete blob {job.blob_name}: {e}")
                            logger.warning(f"[Session {session_id}] [{job.id}] Failed to delete blob: {e}")
                    
                    # Try to cleanup Azure transcription job if created
                    if job.azure_job_id:
                        try:
                            await transcriber.delete_transcription(job.azure_job_id)
                            logger.info(f"[Session {session_id}] [{job.id}] Deleted transcription: {job.azure_job_id}")
                        except Exception as e:
                            errors.append(f"Failed to delete transcription {job.azure_job_id}: {e}")
                            logger.warning(f"[Session {session_id}] [{job.id}] Failed to delete transcription: {e}")
        
        finally:
            if transcriber:
                await transcriber.close()
        
        logger.info(f"[Session {session_id}] Cancelled {cancelled_count} jobs, cleaned {cleaned_blobs} blobs")
        
        return {
            "cancelled": cancelled_count,
            "cleaned_blobs": cleaned_blobs,
            "errors": errors,
        }
    
    @classmethod
    async def delete_session(cls, session_id: str) -> bool:
        """
        Delete a session and all its jobs.
        
        Args:
            session_id: Session ID to delete.
            
        Returns:
            True if deleted, False if not found.
        """
        if session_id in cls._sessions:
            del cls._sessions[session_id]
            logger.debug(f"Deleted session: {session_id}")
            return True
        return False
    
    @classmethod
    def list_all_sessions(cls) -> List[TranscriptionSession]:
        """
        List all sessions (both UI and Bazarr).
        
        Returns:
            List of all TranscriptionSession objects.
        """
        return list(cls._sessions.values())
