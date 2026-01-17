"""
Azure Batch Transcription API Client.

This module handles all interactions with the Azure Speech Services Batch Transcription API.
It provides async methods for:
- Uploading audio to Azure Blob Storage
- Creating transcription jobs
- Monitoring job status
- Retrieving transcription results
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

try:
    from azure.core.exceptions import AzureError
    from azure.storage.blob import (BlobSasPermissions, BlobServiceClient,
                                    generate_blob_sas)
    AZURE_STORAGE_AVAILABLE = True
except ImportError:
    AZURE_STORAGE_AVAILABLE = False
    AzureError = Exception  # Fallback for type hints
    logging.warning("azure-storage-blob not installed. Blob storage features will not work.")

from app.config import get_settings

logger = logging.getLogger(__name__)


class TranscriptionStatus(str, Enum):
    """Transcription job status values."""
    NOT_STARTED = "NotStarted"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"


@dataclass
class TranscriptionJob:
    """Represents a transcription job."""
    id: str
    status: TranscriptionStatus
    display_name: str
    created_at: datetime
    locale: str
    audio_url: str
    self_url: str
    files_url: Optional[str] = None
    error_message: Optional[str] = None
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'TranscriptionJob':
        """Create TranscriptionJob from API response."""
        return cls(
            id=data['self'].split('/')[-1],
            status=TranscriptionStatus(data['status']),
            display_name=data.get('displayName', ''),
            created_at=datetime.fromisoformat(data['createdDateTime'].replace('Z', '+00:00')),
            locale=data.get('locale', 'en-US'),
            audio_url=data.get('contentUrls', [''])[0] if data.get('contentUrls') else '',
            self_url=data['self'],
            files_url=data.get('links', {}).get('files'),
            error_message=data.get('properties', {}).get('error', {}).get('message'),
        )


@dataclass
class TranscriptionSegment:
    """A single segment of transcribed text with timing."""
    start: float  # seconds
    end: float    # seconds
    text: str
    confidence: float = 0.0


@dataclass
class TranscriptionResult:
    """Complete transcription result."""
    job_id: str
    language: str
    segments: List[TranscriptionSegment]
    duration: float  # total audio duration in seconds
    
    @property
    def text(self) -> str:
        """Get full transcription text."""
        return ' '.join(seg.text for seg in self.segments)
    
    def to_srt(self) -> str:
        """Convert transcription to SRT format."""
        from app.utils.subtitle_utils import seconds_to_srt_time
        
        srt_lines = []
        
        for i, segment in enumerate(self.segments, 1):
            start_time = seconds_to_srt_time(segment.start)
            end_time = seconds_to_srt_time(segment.end)
            
            srt_lines.append(str(i))
            srt_lines.append(f"{start_time} --> {end_time}")
            srt_lines.append(segment.text.strip())
            srt_lines.append("")
        
        return "\n".join(srt_lines)
    
    # Note: _seconds_to_srt_time moved to subtitle_utils.seconds_to_srt_time


class AzureBatchTranscriber:
    """
    Client for Azure Batch Transcription API.
    
    Usage:
        transcriber = AzureBatchTranscriber()
        
        # Upload audio and create job
        audio_url = await transcriber.upload_audio("/path/to/audio.wav")
        job = await transcriber.create_transcription(audio_url, "en-US")
        
        # Wait for completion
        result = await transcriber.wait_for_transcription(job.id)
        
        # Get SRT content
        srt_content = result.to_srt()
    """
    
    def __init__(self, speech_key: Optional[str] = None, speech_region: Optional[str] = None):
        """
        Initialize the transcriber.
        
        Args:
            speech_key: Azure Speech API key. If not provided, uses AZURE_SPEECH_KEY env var.
            speech_region: Azure region. If not provided, uses AZURE_SPEECH_REGION env var.
        """
        settings = get_settings()
        self.speech_key = speech_key or settings.azure.speech_key
        self.speech_region = speech_region or settings.azure.speech_region
        self.api_base_url = f"https://{self.speech_region}.api.cognitive.microsoft.com/speechtotext/v3.2"
        
        # Storage settings (for blob upload)
        self.storage_connection_string = settings.azure.storage_connection_string
        self.storage_container = settings.azure.storage_container
        
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        return {
            "Ocp-Apim-Subscription-Key": self.speech_key,
            "Content-Type": "application/json"
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def upload_audio(self, file_path: str) -> tuple[str, str]:
        """
        Upload audio file to Azure Blob Storage and return SAS URL.
        
        Args:
            file_path: Path to the audio file.
            
        Returns:
            Tuple of (SAS URL, blob_name) for the uploaded blob.
        """
        if not AZURE_STORAGE_AVAILABLE:
            raise RuntimeError("azure-storage-blob is not installed. Run: pip install azure-storage-blob")
        
        if not self.storage_connection_string:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING is not configured")
        
        # Create blob client with extended timeouts for large uploads
        # connection_timeout: time to establish connection (30s)
        # read_timeout: time to wait for data during read/write (600s = 10 min)
        blob_service_client = BlobServiceClient.from_connection_string(
            self.storage_connection_string,
            connection_timeout=30,
            read_timeout=600,
            # Use 4MB blocks for chunked uploads (default is 4MB, but explicit)
            max_block_size=4 * 1024 * 1024,
            # Files larger than 64MB will use chunked upload
            max_single_put_size=64 * 1024 * 1024,
        )
        
        # Ensure container exists
        container_client = blob_service_client.get_container_client(self.storage_container)
        try:
            await asyncio.to_thread(container_client.create_container)
            logger.info(f"Created container: {self.storage_container}")
        except Exception:
            pass  # Container already exists
        
        # Generate unique blob name
        file_ext = os.path.splitext(file_path)[1]
        blob_name = f"audio/{uuid.uuid4()}{file_ext}"
        
        # Get file size for logging and upload optimization
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        logger.info(f"Uploading {file_size_mb:.1f} MB audio file to blob: {blob_name}")
        
        # Upload file with retry logic
        blob_client = container_client.get_blob_client(blob_name)
        max_retries = 3
        upload_start = datetime.now(timezone.utc)
        
        for attempt in range(1, max_retries + 1):
            try:
                with open(file_path, 'rb') as f:
                    # Azure SDK will automatically use chunked upload for files > max_single_put_size
                    await asyncio.to_thread(
                        blob_client.upload_blob,
                        f,
                        overwrite=True,
                        max_concurrency=4,  # Parallel upload threads for chunks
                    )
                break  # Success
            except (AzureError, TimeoutError, ConnectionError, OSError) as e:
                last_error = e
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                    logger.warning(
                        f"Upload attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Upload failed after {max_retries} attempts: {e}")
                    raise
        
        upload_duration = (datetime.now(timezone.utc) - upload_start).total_seconds()
        logger.info(f"Uploaded {file_size_mb:.1f} MB to blob in {upload_duration:.1f}s: {blob_name}")
        
        # Get account name and key for SAS generation
        account_name = blob_service_client.account_name
        account_key = blob_service_client.credential.account_key if blob_service_client.credential else None
        
        if not account_name or not account_key:
            raise ValueError("Could not retrieve storage account credentials for SAS generation")
        
        # Generate SAS token (valid for 24 hours)
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=self.storage_container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=24)
        )
        
        sas_url = f"{blob_client.url}?{sas_token}"
        logger.info(f"Generated SAS URL for blob")
        
        return sas_url, blob_name
    
    async def delete_blob(self, blob_name: str) -> bool:
        """
        Delete a blob from Azure Storage.
        
        Args:
            blob_name: Name of the blob to delete.
            
        Returns:
            True if deleted successfully, False otherwise.
        """
        if not AZURE_STORAGE_AVAILABLE:
            logger.warning("azure-storage-blob not installed, cannot delete blob")
            return False
        
        if not self.storage_connection_string:
            logger.warning("Storage not configured, cannot delete blob")
            return False
        
        try:
            blob_service_client = BlobServiceClient.from_connection_string(self.storage_connection_string)
            container_client = blob_service_client.get_container_client(self.storage_container)
            blob_client = container_client.get_blob_client(blob_name)
            await asyncio.to_thread(blob_client.delete_blob)
            logger.info(f"Deleted blob: {blob_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete blob {blob_name}: {e}")
            return False

    async def create_transcription(
        self,
        audio_url: str,
        locale: str = "en-US",
        display_name: Optional[str] = None,
        word_level_timestamps: bool = True,
        diarization: bool = False,
        candidate_locales: Optional[List[str]] = None,
    ) -> TranscriptionJob:
        """
        Create a batch transcription job.
        
        Args:
            audio_url: URL to the audio file (must be accessible by Azure).
            locale: Language locale (e.g., "en-US", "de-DE"). Used as fallback when
                language identification is enabled.
            display_name: Optional display name for the job.
            word_level_timestamps: Enable word-level timing.
            diarization: Enable speaker diarization.
            candidate_locales: Optional list of candidate locales for language identification.
                When provided, enables Azure's language identification feature with "Single"
                mode (at-start detection). Maximum 4 candidates for Single mode.
                Example: ["en-US", "nl-NL", "es-ES", "fr-FR"]
            
        Returns:
            TranscriptionJob object.
        """
        session = await self._get_session()
        
        if display_name is None:
            display_name = f"SubGen-Azure-Batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        payload = {
            "contentUrls": [audio_url],
            "locale": locale,
            "displayName": display_name,
            "properties": {
                "wordLevelTimestampsEnabled": word_level_timestamps,
                "displayFormWordLevelTimestampsEnabled": word_level_timestamps,
                "diarizationEnabled": diarization,
                "punctuationMode": "DictatedAndAutomatic",
                "profanityFilterMode": "None"
            }
        }
        
        # Add language identification if candidate locales provided
        if candidate_locales:
            # Single mode: at-start detection, max 4 candidates
            # Ideal for movies/TV shows with consistent language throughout
            payload["languageIdentification"] = {
                "candidateLocales": candidate_locales[:4],  # Enforce max 4 for Single mode
                "mode": "Single"
            }
            logger.debug(f"Language identification enabled with candidates: {candidate_locales[:4]}")
        
        url = f"{self.api_base_url}/transcriptions"
        logger.debug(f"Creating transcription with URL: {url}")
        logger.debug(f"Audio URL: {audio_url[:100]}..." if len(audio_url) > 100 else f"Audio URL: {audio_url}")
        logger.debug(f"Payload: locale={locale}, displayName={display_name}")
        
        async with session.post(url, headers=self.headers, json=payload) as response:
            if response.status != 201:
                error_text = await response.text()
                raise RuntimeError(f"Failed to create transcription: {response.status} - {error_text}")
            
            data = await response.json()
            job = TranscriptionJob.from_api_response(data)
            logger.info(f"Created transcription job: {job.id}")
            return job
    
    async def get_transcription_status(self, job_id: str) -> TranscriptionJob:
        """
        Get the current status of a transcription job.
        
        Args:
            job_id: The transcription job ID.
            
        Returns:
            Updated TranscriptionJob object.
        """
        session = await self._get_session()
        url = f"{self.api_base_url}/transcriptions/{job_id}"
        
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to get transcription status: {response.status} - {error_text}")
            
            data = await response.json()
            return TranscriptionJob.from_api_response(data)
    
    async def get_transcription_result(self, job_id: str) -> TranscriptionResult:
        """
        Get the transcription result for a completed job.
        
        Args:
            job_id: The transcription job ID.
            
        Returns:
            TranscriptionResult with parsed segments.
        """
        session = await self._get_session()
        
        # First, get the files list
        files_url = f"{self.api_base_url}/transcriptions/{job_id}/files"
        
        async with session.get(files_url, headers=self.headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to get transcription files: {response.status} - {error_text}")
            
            files_data = await response.json()
        
        # Find the transcription result file
        result_file = None
        for file_info in files_data.get('values', []):
            if file_info.get('kind') == 'Transcription':
                result_file = file_info
                break
        
        if not result_file:
            raise RuntimeError(f"No transcription result file found for job {job_id}")
        
        # Download the result
        content_url = result_file['links']['contentUrl']
        
        async with session.get(content_url) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to download transcription result: {response.status} - {error_text}")
            
            result_data = await response.json()
        
        # Parse the result into segments
        segments = []
        duration = 0.0
        detected_locale = None  # Track locale from language identification
        
        for phrase in result_data.get('recognizedPhrases', []):
            # Convert ticks to seconds (1 tick = 100 nanoseconds)
            start_seconds = phrase.get('offsetInTicks', 0) / 10_000_000
            duration_seconds = phrase.get('durationInTicks', 0) / 10_000_000
            end_seconds = start_seconds + duration_seconds
            
            # Extract detected locale from first phrase (when language identification enabled)
            if detected_locale is None and 'locale' in phrase:
                detected_locale = phrase['locale']
                logger.debug(f"Detected language from Azure LID: {detected_locale}")
            
            # Get best transcription
            n_best = phrase.get('nBest', [])
            if n_best:
                text = n_best[0].get('display', '')
                confidence = n_best[0].get('confidence', 0.0)
            else:
                text = ''
                confidence = 0.0
            
            if text:
                segments.append(TranscriptionSegment(
                    start=start_seconds,
                    end=end_seconds,
                    text=text,
                    confidence=confidence
                ))
            
            duration = max(duration, end_seconds)
        
        # Get job info for fallback language
        job = await self.get_transcription_status(job_id)
        
        # Use detected locale from language identification if available, otherwise use job locale
        result_language = detected_locale if detected_locale else job.locale
        
        return TranscriptionResult(
            job_id=job_id,
            language=result_language,
            segments=segments,
            duration=duration
        )
    
    async def wait_for_transcription(
        self,
        job_id: str,
        poll_interval: int = 10,
        timeout: int = 3600
    ) -> TranscriptionResult:
        """
        Wait for a transcription job to complete and return the result.
        
        Args:
            job_id: The transcription job ID.
            poll_interval: Seconds between status checks.
            timeout: Maximum seconds to wait.
            
        Returns:
            TranscriptionResult when job completes.
            
        Raises:
            TimeoutError: If job doesn't complete within timeout.
            RuntimeError: If job fails.
        """
        start_time = asyncio.get_event_loop().time()
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Transcription job {job_id} timed out after {timeout} seconds")
            
            job = await self.get_transcription_status(job_id)
            logger.debug(f"Job {job_id} status: {job.status}")
            
            if job.status == TranscriptionStatus.SUCCEEDED:
                return await self.get_transcription_result(job_id)
            
            if job.status == TranscriptionStatus.FAILED:
                raise RuntimeError(f"Transcription job {job_id} failed: {job.error_message}")
            
            await asyncio.sleep(poll_interval)
    
    async def delete_transcription(self, job_id: str) -> None:
        """
        Delete a transcription job.
        
        Args:
            job_id: The transcription job ID.
            
        Note:
            Azure doesn't support cancelling running transcription jobs.
            If the job is still running, Azure will return a 400 error with 
            "DeleteNotAllowed". This is expected when cancelling and should
            be handled gracefully by the caller.
        """
        session = await self._get_session()
        url = f"{self.api_base_url}/transcriptions/{job_id}"
        
        async with session.delete(url, headers=self.headers) as response:
            if response.status not in (200, 204):
                error_text = await response.text()
                # Check if this is the expected "can't delete running job" error
                if "DeleteNotAllowed" in error_text or "hasn't finished yet" in error_text:
                    logger.info(f"Transcription {job_id} still running on Azure (will complete/expire automatically)")
                else:
                    logger.warning(f"Failed to delete transcription {job_id}: {response.status} - {error_text}")
            else:
                logger.info(f"Deleted transcription job: {job_id}")
    
    async def list_transcriptions(self, top: int = 100) -> List[TranscriptionJob]:
        """
        List recent transcription jobs.
        
        Args:
            top: Maximum number of jobs to return.
            
        Returns:
            List of TranscriptionJob objects.
        """
        session = await self._get_session()
        url = f"{self.api_base_url}/transcriptions?top={top}"
        
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to list transcriptions: {response.status} - {error_text}")
            
            data = await response.json()
            return [TranscriptionJob.from_api_response(item) for item in data.get('values', [])]
    
    async def get_supported_locales(self) -> List[str]:
        """
        Get list of supported language locales.
        
        Returns:
            List of locale strings (e.g., ["en-US", "de-DE"]).
        """
        session = await self._get_session()
        url = f"{self.api_base_url}/transcriptions/locales"
        
        async with session.get(url, headers=self.headers) as response:
            if response.status != 200:
                error_text = await response.text()
                raise RuntimeError(f"Failed to get supported locales: {response.status} - {error_text}")
            
            return await response.json()


# Convenience function for simple transcription
async def transcribe_audio(
    audio_path: str,
    language: str = "en-US",
    output_srt_path: Optional[str] = None
) -> str:
    """
    Convenience function to transcribe an audio file.
    
    Args:
        audio_path: Path to audio file.
        language: Language locale.
        output_srt_path: Optional path to save SRT file.
        
    Returns:
        SRT content as string.
    """
    transcriber = AzureBatchTranscriber()
    blob_name = None
    
    try:
        # Upload audio
        audio_url, blob_name = await transcriber.upload_audio(audio_path)
        
        # Create and wait for transcription
        job = await transcriber.create_transcription(audio_url, language)
        result = await transcriber.wait_for_transcription(job.id)
        
        # Generate SRT
        srt_content = result.to_srt()
        
        # Save if path provided
        if output_srt_path:
            with open(output_srt_path, 'w', encoding='utf-8') as f:
                f.write(srt_content)
        
        # Cleanup job
        await transcriber.delete_transcription(job.id)
        
        return srt_content
        
    finally:
        # Cleanup blob
        if blob_name:
            await transcriber.delete_blob(blob_name)
        await transcriber.close()
