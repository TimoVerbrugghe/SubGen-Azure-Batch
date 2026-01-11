"""
Pytest configuration and fixtures for SubGen-Azure-Batch tests.

This module provides:
- Session-scoped fixtures for environment setup
- Azure credentials fixtures
- Mock fixtures for unit testing
- Temporary file fixtures
- Custom pytest markers
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "azure_api: marks tests requiring real Azure API calls (deselect with '-m \"not azure_api\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks integration tests (deselect with '-m \"not integration\"')"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests that are slow to run"
    )


# Load test environment variables
@pytest.fixture(scope="session", autouse=True)
def load_test_env():
    """Load environment variables from .env in repository root."""
    # Look for .env in the repository root (parent of tests/)
    repo_root = Path(__file__).parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    # Don't skip if .env doesn't exist - allow running unit tests without it


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# Azure Credentials Fixtures
# ============================================================================

@pytest.fixture
def azure_speech_key():
    """Get Azure Speech API key from environment."""
    key = os.getenv("AZURE_SPEECH_KEY")
    if not key or key == "your_azure_speech_key_here":
        pytest.skip("AZURE_SPEECH_KEY not configured in tests/.env")
    return key


@pytest.fixture
def azure_speech_region():
    """Get Azure Speech region from environment."""
    return os.getenv("AZURE_SPEECH_REGION", "swedencentral")


@pytest.fixture
def azure_storage_connection_string():
    """Get Azure Storage connection string from environment."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str or conn_str == "your_storage_connection_string_here":
        pytest.skip("AZURE_STORAGE_CONNECTION_STRING not configured in tests/.env")
    return conn_str


@pytest.fixture
def azure_storage_container():
    """Get Azure Storage container name from environment."""
    return os.getenv("AZURE_STORAGE_CONTAINER", "transcription-audio")


# ============================================================================
# Mock Settings Fixture
# ============================================================================

@pytest.fixture
def mock_settings():
    """Create mock settings for testing without real environment."""
    mock_azure = MagicMock()
    mock_azure.speech_key = "test-key"
    mock_azure.speech_region = "swedencentral"
    mock_azure.storage_connection_string = "test-connection-string"
    mock_azure.storage_container = "test-container"
    mock_azure.is_configured = True
    mock_azure.requires_storage = True
    mock_azure.api_base_url = "https://swedencentral.api.cognitive.microsoft.com/speechtotext/v3.2"
    
    mock_bazarr = MagicMock()
    mock_bazarr.url = "http://localhost:6767"
    mock_bazarr.api_key = "test-bazarr-key"
    mock_bazarr.is_configured = True
    
    mock_plex = MagicMock()
    mock_plex.server = "http://localhost:32400"
    mock_plex.token = "test-plex-token"
    mock_plex.is_configured = True
    
    mock_jellyfin = MagicMock()
    mock_jellyfin.server = "http://localhost:8096"
    mock_jellyfin.token = "test-jellyfin-token"
    mock_jellyfin.is_configured = True
    
    mock_emby = MagicMock()
    mock_emby.server = "http://localhost:8096"
    mock_emby.token = "test-emby-token"
    mock_emby.is_configured = True
    
    mock_processing = MagicMock()
    mock_processing.process_added_media = True
    mock_processing.process_on_play = True
    
    mock_transcription = MagicMock()
    mock_transcription.force_language = ""
    mock_transcription.forced_language = None
    mock_transcription.append_credit_line = False
    mock_transcription.lrc_for_audio_files = True
    mock_transcription.preferred_audio_languages = "eng"
    mock_transcription.preferred_audio_languages_list = ["eng"]
    mock_transcription.limit_to_preferred_audio_languages = False
    mock_transcription.detect_language_length = 30
    mock_transcription.detect_language_offset = 0
    
    mock_skip = MagicMock()
    mock_skip.skip_if_subgen_exists = False
    mock_skip.skip_if_any_subtitle_exists = False
    mock_skip.skip_if_internal_subtitle_exists = False
    mock_skip.enabled_checks = []
    
    mock_subtitle_naming = MagicMock()
    mock_subtitle_naming.naming_type = "ISO_639_2_B"
    mock_subtitle_naming.show_subgen_marker = False
    mock_subtitle_naming.language_name_override = ""
    mock_subtitle_naming.valid_types = ("ISO_639_1", "ISO_639_2_T", "ISO_639_2_B", "NAME", "NATIVE")
    mock_subtitle_naming.is_valid = True
    
    mock_path_mapping = MagicMock()
    mock_path_mapping.enabled = False
    mock_path_mapping.from_path = "/tv"
    mock_path_mapping.to_path = "/Volumes/TV"
    mock_path_mapping.apply = lambda path: path
    
    mock_notification = MagicMock()
    mock_notification.pushover_user_key = ""
    mock_notification.pushover_api_token = ""
    mock_notification.notify_on_failure = True
    mock_notification.pushover_configured = False
    mock_notification.is_configured = False
    
    settings = MagicMock()
    settings.azure = mock_azure
    settings.bazarr = mock_bazarr
    settings.plex = mock_plex
    settings.jellyfin = mock_jellyfin
    settings.emby = mock_emby
    settings.processing = mock_processing
    settings.transcription = mock_transcription
    settings.skip = mock_skip
    settings.subtitle_naming = mock_subtitle_naming
    settings.path_mapping = mock_path_mapping
    settings.notification = mock_notification
    settings.subtitle_language = "en"
    settings.media_folders = ["/media/tv", "/media/movies"]
    settings.concurrent_jobs = 2
    settings.transcode_dir = ""  # Empty string means use system temp
    settings.host = "0.0.0.0"
    settings.port = 8090
    settings.debug = False
    
    return settings


@pytest.fixture
def patched_settings(mock_settings):
    """Patch get_settings to return mock settings."""
    with patch('app.config.get_settings', return_value=mock_settings):
        yield mock_settings


# ============================================================================
# Temporary File Fixtures
# ============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def temp_video_file(temp_dir) -> Generator[str, None, None]:
    """Create a dummy video file for testing."""
    video_path = os.path.join(temp_dir, "test_video.mkv")
    # Create minimal file with video-like content
    with open(video_path, 'wb') as f:
        # Write minimal MKV header magic bytes
        f.write(b'\x1a\x45\xdf\xa3' + b'\x00' * 100)
    yield video_path


@pytest.fixture
def temp_audio_file(temp_dir) -> Generator[str, None, None]:
    """Create a dummy audio file for testing."""
    audio_path = os.path.join(temp_dir, "test_audio.mp3")
    with open(audio_path, 'wb') as f:
        # Write minimal MP3 header
        f.write(b'\xff\xfb\x90\x00' + b'\x00' * 100)
    yield audio_path


@pytest.fixture
def temp_srt_file(temp_dir) -> Generator[str, None, None]:
    """Create a temporary SRT file with sample content."""
    srt_path = os.path.join(temp_dir, "test.srt")
    content = """1
00:00:00,000 --> 00:00:02,500
Hello world

2
00:00:03,000 --> 00:00:05,500
This is a test
"""
    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write(content)
    yield srt_path


@pytest.fixture
def sample_srt_content():
    """Return sample SRT content for testing."""
    return """1
00:00:00,000 --> 00:00:02,500
Hello world

2
00:00:03,000 --> 00:00:05,500
This is a test

3
00:01:00,000 --> 00:01:05,000
This is the third subtitle
"""


# ============================================================================
# Test Media Files
# ============================================================================

@pytest.fixture
def test_audio_file():
    """Get path to test audio file."""
    path = os.getenv("TEST_AUDIO_FILE")
    if not path or not Path(path).exists():
        pytest.skip("TEST_AUDIO_FILE not configured or file doesn't exist")
    return path


@pytest.fixture
def test_video_file():
    """Get path to test video file."""
    path = os.getenv("TEST_VIDEO_FILE")
    if not path or not Path(path).exists():
        pytest.skip("TEST_VIDEO_FILE not configured or file doesn't exist")
    return path


@pytest.fixture
def sample_audio_content():
    """Generate a simple audio file for testing (requires pydub)."""
    try:
        import tempfile as tf

        from pydub import AudioSegment
        from pydub.generators import Sine

        # Generate 3 seconds of a sine wave tone
        tone = Sine(440).to_audio_segment(duration=3000)
        
        # Export to temporary file
        with tf.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tone.export(f.name, format="wav")
            yield f.name
        
        # Cleanup
        os.unlink(f.name)
    except ImportError:
        pytest.skip("pydub not installed, cannot generate sample audio")


# ============================================================================
# Mock Async Client Fixtures
# ============================================================================

@pytest.fixture
def mock_aiohttp_session():
    """Create a mock aiohttp session for testing HTTP clients."""
    session = AsyncMock()
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    session.get = MagicMock(return_value=response)
    session.post = MagicMock(return_value=response)
    session.put = MagicMock(return_value=response)
    session.patch = MagicMock(return_value=response)
    session.delete = MagicMock(return_value=response)
    session.close = AsyncMock()
    session.closed = False
    return session


@pytest.fixture
def mock_transcription_result():
    """Create a mock transcription result."""
    from app.azure_batch_transcriber import (TranscriptionResult,
                                             TranscriptionSegment)
    
    return TranscriptionResult(
        job_id='test-job-123',
        language='en-US',
        segments=[
            TranscriptionSegment(start=0.0, end=2.5, text='Hello world', confidence=0.95),
            TranscriptionSegment(start=3.0, end=5.5, text='This is a test', confidence=0.92),
            TranscriptionSegment(start=6.0, end=10.0, text='Testing transcription service', confidence=0.90),
        ],
        duration=10.0
    )


# ============================================================================
# FastAPI Test Client Fixture
# ============================================================================

@pytest.fixture
def app_client():
    """Create FastAPI test client."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)
