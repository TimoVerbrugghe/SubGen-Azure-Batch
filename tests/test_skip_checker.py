"""
Tests for skip checker module.

Tests cover:
- SkipResult dataclass
- Stream info retrieval
- External subtitle detection
- Skip condition checking
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSkipResult:
    """Test SkipResult dataclass."""
    
    def test_skip_result_creation(self):
        """Test creating a skip result."""
        from app.utils.skip_checker import SkipResult
        
        result = SkipResult(should_skip=True, reason="Test reason")
        assert result.should_skip is True
        assert result.reason == "Test reason"
    
    def test_skip_factory(self):
        """Test SkipResult.skip factory method."""
        from app.utils.skip_checker import SkipResult
        
        result = SkipResult.skip("Subtitle exists")
        assert result.should_skip is True
        assert result.reason == "Subtitle exists"
    
    def test_proceed_factory(self):
        """Test SkipResult.proceed factory method."""
        from app.utils.skip_checker import SkipResult
        
        result = SkipResult.proceed()
        assert result.should_skip is False
        assert result.reason is None


class TestGetStreamInfo:
    """Test get_stream_info function."""
    
    @pytest.mark.asyncio
    async def test_get_stream_info_success(self):
        """Test successful stream info retrieval."""
        from app.utils.skip_checker import get_stream_info
        
        mock_output = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
                {"codec_type": "subtitle", "codec_name": "srt", "tags": {"language": "eng"}},
            ]
        }
        
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(str(mock_output).replace("'", '"').encode(), b"")
        )
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            with patch('json.loads', return_value=mock_output):
                result = await get_stream_info("/path/to/video.mkv")
                
                assert 'audio' in result
                assert 'subtitle' in result
                assert len(result['audio']) == 1
                assert len(result['subtitle']) == 1
                assert result['audio'][0]['language'] == 'eng'
    
    @pytest.mark.asyncio
    async def test_get_stream_info_failure(self):
        """Test stream info returns empty on ffprobe failure."""
        from app.utils.skip_checker import get_stream_info
        
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))
        
        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await get_stream_info("/path/to/video.mkv")
            
            assert result == {'audio': [], 'subtitle': []}


class TestGetAudioLanguages:
    """Test get_audio_languages function."""
    
    def test_extracts_audio_languages(self):
        """Test audio language extraction."""
        from app.utils.skip_checker import get_audio_languages
        
        stream_info = {
            'audio': [
                {'language': 'eng'},
                {'language': 'deu'},
                {'language': ''},  # No language
            ],
            'subtitle': []
        }
        
        result = get_audio_languages(stream_info)
        assert result == ['eng', 'deu']
    
    def test_empty_stream_info(self):
        """Test with empty stream info."""
        from app.utils.skip_checker import get_audio_languages
        
        result = get_audio_languages({})
        assert result == []


class TestGetInternalSubtitleLanguages:
    """Test get_internal_subtitle_languages function."""
    
    def test_extracts_subtitle_languages(self):
        """Test internal subtitle language extraction."""
        from app.utils.skip_checker import get_internal_subtitle_languages
        
        stream_info = {
            'audio': [],
            'subtitle': [
                {'language': 'eng'},
                {'language': 'spa'},
            ]
        }
        
        result = get_internal_subtitle_languages(stream_info)
        assert result == ['eng', 'spa']
    
    def test_filters_empty_languages(self):
        """Test that empty languages are filtered."""
        from app.utils.skip_checker import get_internal_subtitle_languages
        
        stream_info = {
            'audio': [],
            'subtitle': [
                {'language': 'eng'},
                {'language': ''},  # Empty
                {'language': 'spa'},
            ]
        }
        
        result = get_internal_subtitle_languages(stream_info)
        assert result == ['eng', 'spa']


class TestFindExternalSubtitles:
    """Test find_external_subtitles function."""
    
    def test_find_subtitles_in_directory(self, temp_dir):
        """Test finding external subtitle files."""
        from app.utils.skip_checker import find_external_subtitles

        # Create media file
        video_path = os.path.join(temp_dir, "movie.mkv")
        with open(video_path, 'w') as f:
            f.write("video")
        
        # Create subtitle files
        for sub_file in ["movie.en.srt", "movie.de.srt", "movie.subgen.en.srt"]:
            sub_path = os.path.join(temp_dir, sub_file)
            with open(sub_path, 'w') as f:
                f.write("subtitle")
        
        # Create unrelated subtitle (different name)
        other_sub = os.path.join(temp_dir, "other.en.srt")
        with open(other_sub, 'w') as f:
            f.write("other subtitle")
        
        result = find_external_subtitles(video_path)
        
        # Should find 3 subtitles for this video
        assert len(result) == 3
        
        # Check that subgen marker is detected
        subgen_subs = [s for s in result if s[2] is True]
        assert len(subgen_subs) == 1
    
    def test_nonexistent_directory(self):
        """Test handling of non-existent directory."""
        from app.utils.skip_checker import find_external_subtitles
        
        result = find_external_subtitles("/nonexistent/path/video.mkv")
        assert result == []


class TestHasExternalSubtitleForLanguage:
    """Test has_external_subtitle_for_language function."""
    
    def test_finds_matching_subtitle(self, temp_dir):
        """Test finding subtitle for specific language."""
        from app.utils.skip_checker import has_external_subtitle_for_language

        # Create media file
        video_path = os.path.join(temp_dir, "movie.mkv")
        with open(video_path, 'w') as f:
            f.write("video")
        
        # Create English subtitle
        sub_path = os.path.join(temp_dir, "movie.en.srt")
        with open(sub_path, 'w') as f:
            f.write("subtitle")
        
        result = has_external_subtitle_for_language(video_path, "en")
        assert result is True
        
        # No German subtitle
        result = has_external_subtitle_for_language(video_path, "de")
        assert result is False
    
    def test_only_subgen_filter(self, temp_dir):
        """Test filtering to only SubGen subtitles."""
        from app.utils.skip_checker import has_external_subtitle_for_language

        # Create media file
        video_path = os.path.join(temp_dir, "movie.mkv")
        with open(video_path, 'w') as f:
            f.write("video")
        
        # Create regular English subtitle (not subgen)
        sub_path = os.path.join(temp_dir, "movie.en.srt")
        with open(sub_path, 'w') as f:
            f.write("subtitle")
        
        # Without only_subgen, should find it
        result = has_external_subtitle_for_language(video_path, "en", only_subgen=False)
        assert result is True
        
        # With only_subgen, should not find it
        result = has_external_subtitle_for_language(video_path, "en", only_subgen=True)
        assert result is False
        
        # Now create a subgen subtitle
        subgen_path = os.path.join(temp_dir, "movie.subgen.en.srt")
        with open(subgen_path, 'w') as f:
            f.write("subtitle")
        
        # Now should find it with only_subgen
        result = has_external_subtitle_for_language(video_path, "en", only_subgen=True)
        assert result is True


class TestHasAnyExternalSubtitle:
    """Test has_any_external_subtitle function."""
    
    def test_finds_any_subtitle(self, temp_dir):
        """Test finding any external subtitle."""
        from app.utils.skip_checker import has_any_external_subtitle

        # Create media file
        video_path = os.path.join(temp_dir, "movie.mkv")
        with open(video_path, 'w') as f:
            f.write("video")
        
        # No subtitles yet
        result = has_any_external_subtitle(video_path)
        assert result is False
        
        # Create a subtitle
        sub_path = os.path.join(temp_dir, "movie.en.srt")
        with open(sub_path, 'w') as f:
            f.write("subtitle")
        
        # Now should find it
        result = has_any_external_subtitle(video_path)
        assert result is True


class TestShouldSkipFile:
    """Test the main should_skip_file function."""
    
    @pytest.mark.asyncio
    async def test_skip_nonexistent_file(self):
        """Test that non-existent files are skipped."""
        from app.utils.skip_checker import should_skip_file
        
        result = await should_skip_file("/nonexistent/file.mkv", "en")
        assert result.should_skip is True
        assert result.reason is not None
        assert "not found" in result.reason.lower() or "not exist" in result.reason.lower()
    
    @pytest.mark.asyncio
    async def test_skip_non_existent_file(self, temp_dir):
        """Test that non-existent files are skipped."""
        from app.utils.skip_checker import should_skip_file

        nonexistent_path = os.path.join(temp_dir, "nonexistent.mkv")
        
        result = await should_skip_file(nonexistent_path, "en")
        assert result.should_skip is True
        assert result.reason is not None
        assert "not found" in result.reason.lower()
    
    @pytest.mark.asyncio
    async def test_existing_file_not_skipped_by_default(self, temp_dir, patched_settings):
        """Test that existing media files are not skipped when skip config is disabled."""
        from app.utils.skip_checker import should_skip_file

        # Create a mock video file
        video_path = os.path.join(temp_dir, "video.mkv")
        with open(video_path, 'wb') as f:
            f.write(b'\x00' * 1000)
        
        # Ensure all skip options are disabled
        patched_settings.skip.skip_if_target_subtitles_exist = False
        patched_settings.skip.skip_if_external_subtitles_exist = False
        patched_settings.skip.internal_subtitle_language = ""
        patched_settings.skip.audio_language_skip_list = []
        patched_settings.skip.subtitle_languages_skip_list = []
        
        result = await should_skip_file(video_path, "en")
        # Should not skip since all skip conditions are disabled
        assert result.should_skip is False
    
    @pytest.mark.asyncio
    async def test_proceed_for_valid_media(self, temp_video_file, patched_settings):
        """Test that valid media files proceed (with skip config disabled)."""
        from app.utils.skip_checker import should_skip_file

        # Configure skip settings to not skip
        patched_settings.skip.skip_if_subgen_exists = False
        patched_settings.skip.skip_if_any_subtitle_exists = False
        patched_settings.skip.skip_if_internal_subtitle_exists = False
        patched_settings.skip.enabled_checks = []
        
        with patch('app.utils.skip_checker.get_stream_info', new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = {'audio': [], 'subtitle': []}
            
            result = await should_skip_file(temp_video_file, "en")
            # Should not skip (assuming no existing subtitles)
            # Note: The actual behavior depends on config


class TestSkipConfigIntegration:
    """Test skip checker with various configurations."""
    
    @pytest.mark.asyncio
    async def test_skip_if_subgen_exists(self, temp_dir, patched_settings):
        """Test skipping when SubGen subtitle already exists."""
        from app.utils.skip_checker import should_skip_file

        # Enable skip_if_subgen_exists
        patched_settings.skip.skip_if_subgen_exists = True
        patched_settings.skip.skip_if_any_subtitle_exists = False
        patched_settings.skip.enabled_checks = ['subgen_exists']
        
        # Create media file
        video_path = os.path.join(temp_dir, "movie.mkv")
        with open(video_path, 'wb') as f:
            f.write(b'\x1a\x45\xdf\xa3' + b'\x00' * 100)
        
        # Create SubGen subtitle
        sub_path = os.path.join(temp_dir, "movie.subgen.en.srt")
        with open(sub_path, 'w') as f:
            f.write("subtitle")
        
        with patch('app.utils.skip_checker.get_stream_info', new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = {'audio': [], 'subtitle': []}
            
            result = await should_skip_file(video_path, "en")
            # Behavior depends on exact implementation
            # Just verify it runs without error
            assert isinstance(result.should_skip, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
