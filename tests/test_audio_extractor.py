"""
Tests for audio extraction module.

Tests cover:
- File type detection functions
- Audio extraction (with mocked FFmpeg)
- Duration and info retrieval
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFileTypeDetection:
    """Test file type detection functions."""
    
    def test_is_video_file_with_video_extensions(self):
        """Test video file detection with valid extensions."""
        from app.audio_extractor import is_video_file
        
        assert is_video_file("/path/to/movie.mp4") is True
        assert is_video_file("/path/to/movie.mkv") is True
        assert is_video_file("/path/to/movie.avi") is True
        assert is_video_file("/path/to/movie.mov") is True
        assert is_video_file("/path/to/movie.wmv") is True
        assert is_video_file("/path/to/movie.ts") is True
        assert is_video_file("/path/to/movie.m2ts") is True
    
    def test_is_video_file_with_non_video(self):
        """Test video file detection returns False for non-video files."""
        from app.audio_extractor import is_video_file
        
        assert is_video_file("/path/to/song.mp3") is False
        assert is_video_file("/path/to/document.txt") is False
        assert is_video_file("/path/to/image.jpg") is False
        assert is_video_file("/path/to/subtitle.srt") is False
    
    def test_is_video_file_case_insensitive(self):
        """Test that extension detection is case-insensitive."""
        from app.audio_extractor import is_video_file
        
        assert is_video_file("/path/to/movie.MP4") is True
        assert is_video_file("/path/to/movie.MKV") is True
        assert is_video_file("/path/to/movie.Mkv") is True
    
    def test_is_audio_file_with_audio_extensions(self):
        """Test audio file detection with valid extensions."""
        from app.audio_extractor import is_audio_file
        
        assert is_audio_file("/path/to/song.mp3") is True
        assert is_audio_file("/path/to/song.wav") is True
        assert is_audio_file("/path/to/song.flac") is True
        assert is_audio_file("/path/to/song.aac") is True
        assert is_audio_file("/path/to/song.ogg") is True
        assert is_audio_file("/path/to/song.m4a") is True
        assert is_audio_file("/path/to/song.opus") is True
    
    def test_is_audio_file_with_non_audio(self):
        """Test audio file detection returns False for non-audio files."""
        from app.audio_extractor import is_audio_file
        
        assert is_audio_file("/path/to/movie.mp4") is False
        assert is_audio_file("/path/to/document.txt") is False
    
    def test_is_media_file(self):
        """Test combined media file detection."""
        from app.audio_extractor import is_media_file

        # Videos should be recognized
        assert is_media_file("/path/to/movie.mp4") is True
        assert is_media_file("/path/to/movie.mkv") is True
        
        # Audio should be recognized
        assert is_media_file("/path/to/song.mp3") is True
        assert is_media_file("/path/to/song.flac") is True
        
        # Non-media should not be recognized
        assert is_media_file("/path/to/document.txt") is False
        assert is_media_file("/path/to/subtitle.srt") is False


class TestMediaExtensions:
    """Test media extension constants."""
    
    def test_video_extensions_not_empty(self):
        """Test that video extensions set is not empty."""
        from app.audio_extractor import VIDEO_EXTENSIONS
        
        assert len(VIDEO_EXTENSIONS) > 0
        assert '.mp4' in VIDEO_EXTENSIONS
        assert '.mkv' in VIDEO_EXTENSIONS
    
    def test_audio_extensions_not_empty(self):
        """Test that audio extensions set is not empty."""
        from app.audio_extractor import AUDIO_EXTENSIONS
        
        assert len(AUDIO_EXTENSIONS) > 0
        assert '.mp3' in AUDIO_EXTENSIONS
        assert '.wav' in AUDIO_EXTENSIONS
    
    def test_media_extensions_is_union(self):
        """Test that MEDIA_EXTENSIONS is union of video and audio."""
        from app.audio_extractor import (AUDIO_EXTENSIONS, MEDIA_EXTENSIONS,
                                         VIDEO_EXTENSIONS)
        
        assert MEDIA_EXTENSIONS == VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


class TestGetMediaDuration:
    """Test get_media_duration function."""
    
    @pytest.mark.asyncio
    async def test_get_duration_success(self):
        """Test successful duration retrieval."""
        from app.audio_extractor import get_media_duration
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"123.456\n", b""))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            duration = await get_media_duration("/path/to/video.mp4")
            assert duration == pytest.approx(123.456, 0.001)
    
    @pytest.mark.asyncio
    async def test_get_duration_returns_zero_on_failure(self):
        """Test that duration returns 0.0 on ffprobe failure."""
        from app.audio_extractor import get_media_duration
        
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            duration = await get_media_duration("/path/to/video.mp4")
            assert duration == 0.0
    
    @pytest.mark.asyncio
    async def test_get_duration_handles_exception(self):
        """Test that exceptions are caught and 0.0 is returned."""
        from app.audio_extractor import get_media_duration
        
        with patch('asyncio.create_subprocess_exec', side_effect=Exception("Test error")):
            duration = await get_media_duration("/path/to/video.mp4")
            assert duration == 0.0


class TestGetAudioInfo:
    """Test get_audio_info function."""
    
    @pytest.mark.asyncio
    async def test_get_audio_info_success(self):
        """Test successful audio info retrieval."""
        from app.audio_extractor import get_audio_info
        
        mock_response = {
            "streams": [{
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "bit_rate": "128000"
            }]
        }
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(str(mock_response).replace("'", '"').encode(), b"")
        )
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            with patch('json.loads', return_value=mock_response):
                info = await get_audio_info("/path/to/video.mp4")
                assert info.get('codec_name') == 'aac'
    
    @pytest.mark.asyncio
    async def test_get_audio_info_returns_empty_on_failure(self):
        """Test that empty dict is returned on ffprobe failure."""
        from app.audio_extractor import get_audio_info
        
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            info = await get_audio_info("/path/to/video.mp4")
            assert info == {}


class TestExtractAudio:
    """Test extract_audio function."""
    
    @pytest.mark.asyncio
    async def test_extract_audio_file_not_found(self):
        """Test that FileNotFoundError is raised for missing file."""
        from app.audio_extractor import extract_audio
        
        with pytest.raises(FileNotFoundError):
            await extract_audio("/nonexistent/video.mp4")
    
    @pytest.mark.asyncio
    async def test_extract_audio_success(self, temp_video_file):
        """Test successful audio extraction with mocked FFmpeg."""
        from app.audio_extractor import extract_audio
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            output_path = await extract_audio(temp_video_file, output_format='wav')
            assert output_path.endswith('.wav')
    
    @pytest.mark.asyncio
    async def test_extract_audio_ffmpeg_failure(self, temp_video_file):
        """Test RuntimeError is raised on FFmpeg failure."""
        from app.audio_extractor import extract_audio
        
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"FFmpeg error"))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            with pytest.raises(RuntimeError, match="Audio extraction failed"):
                await extract_audio(temp_video_file)
    
    @pytest.mark.asyncio
    async def test_extract_audio_ffmpeg_not_found(self, temp_video_file):
        """Test RuntimeError when FFmpeg is not installed."""
        from app.audio_extractor import extract_audio
        
        with patch('asyncio.create_subprocess_exec', side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="FFmpeg not found"):
                await extract_audio(temp_video_file)


class TestPrepareAudioForTranscription:
    """Test prepare_audio_for_transcription function."""
    
    @pytest.mark.asyncio
    async def test_unsupported_file_raises_error(self):
        """Test ValueError is raised for unsupported file types."""
        from app.audio_extractor import prepare_audio_for_transcription
        
        with pytest.raises(ValueError, match="Unsupported media file"):
            await prepare_audio_for_transcription("/path/to/document.txt")
    
    @pytest.mark.asyncio
    async def test_audio_file_same_format_no_conversion(self, temp_audio_file):
        """Test that audio files with correct format are not converted."""
        from app.audio_extractor import prepare_audio_for_transcription

        # Rename to .wav to match target format
        wav_path = temp_audio_file.replace('.mp3', '.wav')
        os.rename(temp_audio_file, wav_path)
        
        try:
            audio_path, is_temp = await prepare_audio_for_transcription(
                wav_path, target_format='wav'
            )
            assert audio_path == wav_path
            assert is_temp is False
        finally:
            if os.path.exists(wav_path):
                pass  # Will be cleaned up by fixture


class TestCleanupTempFile:
    """Test cleanup_temp_file function."""
    
    def test_cleanup_existing_file(self, temp_dir):
        """Test cleanup of existing temp file."""
        from app.audio_extractor import cleanup_temp_file
        
        temp_file = os.path.join(temp_dir, "temp_audio.wav")
        with open(temp_file, 'w') as f:
            f.write("test")
        
        assert os.path.exists(temp_file)
        cleanup_temp_file(temp_file)
        assert not os.path.exists(temp_file)
    
    def test_cleanup_nonexistent_file_no_error(self):
        """Test cleanup of non-existent file doesn't raise error."""
        from app.audio_extractor import cleanup_temp_file

        # Should not raise
        cleanup_temp_file("/nonexistent/file.wav")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
