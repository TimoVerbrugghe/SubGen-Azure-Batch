"""
Tests for subtitle utilities.

Comprehensive tests for:
- SRT time format conversion
- SRT parsing and validation
- Subtitle file operations
- Language formatting for filenames
- LRC file operations
- Subtitle existence checking
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.subtitle_utils import (SUBTITLE_EXTENSIONS, SubtitleEntry,
                                add_subgen_marker, append_credit_line,
                                entries_to_srt, find_existing_subtitles,
                                format_language_for_filename, get_lrc_path,
                                get_srt_path, load_srt, parse_srt, save_lrc,
                                save_srt, seconds_to_srt_time,
                                srt_time_to_seconds, subtitle_exists,
                                validate_srt, write_lrc)


class TestSrtTimeConversion:
    """Test SRT time format conversion."""
    
    def test_seconds_to_srt_time_zero(self):
        """Test converting 0 seconds."""
        assert seconds_to_srt_time(0) == "00:00:00,000"
    
    def test_seconds_to_srt_time_with_millis(self):
        """Test converting seconds with milliseconds."""
        assert seconds_to_srt_time(1.5) == "00:00:01,500"
        assert seconds_to_srt_time(65.123) == "00:01:05,123"
    
    def test_seconds_to_srt_time_hours(self):
        """Test converting hours."""
        assert seconds_to_srt_time(3661.5) == "01:01:01,500"
    
    def test_srt_time_to_seconds_zero(self):
        """Test converting 00:00:00,000."""
        assert srt_time_to_seconds("00:00:00,000") == 0.0
    
    def test_srt_time_to_seconds_with_millis(self):
        """Test converting time with milliseconds."""
        assert srt_time_to_seconds("00:00:01,500") == 1.5
        assert srt_time_to_seconds("00:01:05,123") == pytest.approx(65.123, 0.001)
    
    def test_srt_time_to_seconds_hours(self):
        """Test converting hours."""
        assert srt_time_to_seconds("01:01:01,500") == 3661.5
    
    def test_roundtrip(self):
        """Test converting back and forth."""
        original = 3723.456
        srt_time = seconds_to_srt_time(original)
        converted = srt_time_to_seconds(srt_time)
        assert converted == pytest.approx(original, 0.001)


class TestParseSrt:
    """Test SRT parsing."""
    
    def test_parse_simple_srt(self):
        """Test parsing a simple SRT file."""
        content = """1
00:00:00,000 --> 00:00:02,500
Hello world

2
00:00:03,000 --> 00:00:05,500
This is a test
"""
        entries = parse_srt(content)
        
        assert len(entries) == 2
        assert entries[0].index == 1
        assert entries[0].text == "Hello world"
        assert entries[0].start_time == "00:00:00,000"
        assert entries[0].end_time == "00:00:02,500"
        
        assert entries[1].index == 2
        assert entries[1].text == "This is a test"
    
    def test_parse_multiline_text(self):
        """Test parsing SRT with multiline text."""
        content = """1
00:00:00,000 --> 00:00:05,000
Line one
Line two
"""
        entries = parse_srt(content)
        
        assert len(entries) == 1
        assert "Line one" in entries[0].text
        assert "Line two" in entries[0].text
    
    def test_parse_empty_content(self):
        """Test parsing empty content."""
        entries = parse_srt("")
        assert len(entries) == 0


class TestValidateSrt:
    """Test SRT validation."""
    
    def test_valid_srt(self):
        """Test validating correct SRT."""
        content = """1
00:00:00,000 --> 00:00:02,500
Hello world
"""
        is_valid, error = validate_srt(content)
        assert is_valid is True
        assert error is None
    
    def test_empty_content(self):
        """Test validating empty content."""
        is_valid, error = validate_srt("")
        assert is_valid is False
        assert error is not None and "Empty" in error
    
    def test_invalid_timing(self):
        """Test validating SRT with end before start."""
        content = """1
00:00:05,000 --> 00:00:02,000
Wrong timing
"""
        is_valid, error = validate_srt(content)
        assert is_valid is False


class TestSaveLoadSrt:
    """Test saving and loading SRT files."""
    
    def test_save_and_load(self, mock_settings):
        """Test saving and loading an SRT file."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        
        content = """1
00:00:00,000 --> 00:00:02,500
Hello world
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            
            with patch('app.config.get_settings', return_value=mock_settings):
                # Save SRT
                srt_path = save_srt(content, media_path, language="en")
            
            assert os.path.exists(srt_path)
            assert srt_path.endswith(".en.srt")
            
            # Load SRT
            loaded = load_srt(srt_path)
            assert "Hello world" in loaded


class TestGetSrtPath:
    """Test SRT path generation."""
    
    def test_basic_path(self, mock_settings):
        """Test basic SRT path generation."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_srt_path("/media/movie.mp4", "en")
            assert path == "/media/movie.en.srt"
    
    def test_with_suffix(self, mock_settings):
        """Test SRT path with suffix."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_srt_path("/media/movie.mp4", "en", suffix="hi")
            assert path == "/media/movie.en.hi.srt"
    
    def test_mkv_file(self, mock_settings):
        """Test with MKV file."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_srt_path("/tv/show/episode.mkv", "de")
            assert path == "/tv/show/episode.de.srt"
    
    def test_with_subgen_marker(self, mock_settings):
        """Test SRT path with subgen marker enabled."""
        mock_settings.subtitle_naming.show_subgen_marker = True
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_srt_path("/media/movie.mp4", "en")
            assert "subgen" in path


class TestSubtitleExtensions:
    """Test subtitle extension constants."""
    
    def test_common_extensions_present(self):
        """Test that common subtitle extensions are defined."""
        assert '.srt' in SUBTITLE_EXTENSIONS
        assert '.vtt' in SUBTITLE_EXTENSIONS
        assert '.ass' in SUBTITLE_EXTENSIONS
        assert '.sub' in SUBTITLE_EXTENSIONS
    
    def test_all_lowercase(self):
        """Test that all extensions are lowercase."""
        for ext in SUBTITLE_EXTENSIONS:
            assert ext == ext.lower()
    
    def test_all_start_with_dot(self):
        """Test that all extensions start with a dot."""
        for ext in SUBTITLE_EXTENSIONS:
            assert ext.startswith('.')


class TestSubtitleEntry:
    """Test SubtitleEntry dataclass."""
    
    def test_creation(self):
        """Test creating a SubtitleEntry."""
        entry = SubtitleEntry(
            index=1,
            start_time="00:00:00,000",
            end_time="00:00:02,500",
            text="Hello world"
        )
        assert entry.index == 1
        assert entry.text == "Hello world"
    
    def test_to_srt(self):
        """Test converting entry to SRT format."""
        entry = SubtitleEntry(
            index=1,
            start_time="00:00:00,000",
            end_time="00:00:02,500",
            text="Hello world"
        )
        srt = entry.to_srt()
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:02,500" in srt
        assert "Hello world" in srt


class TestEntriesToSrt:
    """Test converting entries list to SRT."""
    
    def test_single_entry(self):
        """Test converting single entry."""
        entries = [SubtitleEntry(1, "00:00:00,000", "00:00:02,000", "Test")]
        content = entries_to_srt(entries)
        assert "1\n" in content
        assert "Test" in content
    
    def test_multiple_entries(self):
        """Test converting multiple entries."""
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:02,000", "First"),
            SubtitleEntry(2, "00:00:03,000", "00:00:05,000", "Second"),
        ]
        content = entries_to_srt(entries)
        assert "First" in content
        assert "Second" in content
    
    def test_reindexing(self):
        """Test that entries are re-indexed."""
        entries = [
            SubtitleEntry(5, "00:00:00,000", "00:00:02,000", "First"),
            SubtitleEntry(10, "00:00:03,000", "00:00:05,000", "Second"),
        ]
        content = entries_to_srt(entries)
        # After reindexing, entry indices should be 1 and 2
        assert entries[0].index == 1
        assert entries[1].index == 2


class TestFormatLanguageForFilename:
    """Test language formatting for filenames."""
    
    def test_iso_639_1(self, mock_settings):
        """Test ISO 639-1 formatting."""
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            result = format_language_for_filename("english")
            assert result == "en"
    
    def test_iso_639_2_t(self, mock_settings):
        """Test ISO 639-2/T formatting."""
        mock_settings.subtitle_naming.naming_type = "ISO_639_2_T"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            result = format_language_for_filename("en")
            assert result == "eng"
    
    def test_name_format(self, mock_settings):
        """Test NAME formatting."""
        mock_settings.subtitle_naming.naming_type = "NAME"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            result = format_language_for_filename("en")
            assert result == "English"
    
    def test_override_takes_precedence(self, mock_settings):
        """Test that language name override takes precedence."""
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = "custom"
        with patch('app.config.get_settings', return_value=mock_settings):
            result = format_language_for_filename("en")
            assert result == "custom"
    
    def test_unknown_language(self, mock_settings):
        """Test handling unknown language code."""
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            result = format_language_for_filename("xyz123")
            assert result == "xyz123"  # Returns as-is


class TestFindExistingSubtitles:
    """Test finding existing subtitle files."""
    
    def test_no_subtitles(self):
        """Test when no subtitles exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            open(media_path, 'w').close()  # Create empty file
            
            result = find_existing_subtitles(media_path)
            assert result == []
    
    def test_find_single_subtitle(self):
        """Test finding a single subtitle file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            srt_path = os.path.join(tmpdir, "video.en.srt")
            
            open(media_path, 'w').close()
            open(srt_path, 'w').close()
            
            result = find_existing_subtitles(media_path)
            assert len(result) == 1
            assert result[0][1] == "en"
    
    def test_find_multiple_subtitles(self):
        """Test finding multiple subtitle files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            
            open(media_path, 'w').close()
            open(os.path.join(tmpdir, "video.en.srt"), 'w').close()
            open(os.path.join(tmpdir, "video.es.srt"), 'w').close()
            
            result = find_existing_subtitles(media_path)
            assert len(result) == 2
            languages = [r[1] for r in result]
            assert "en" in languages
            assert "es" in languages
    
    def test_nonexistent_directory(self):
        """Test with non-existent directory."""
        result = find_existing_subtitles("/nonexistent/path/video.mp4")
        assert result == []


class TestSubtitleExists:
    """Test subtitle existence checking."""
    
    def test_subtitle_not_exists(self):
        """Test when subtitle does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            open(media_path, 'w').close()
            
            result = subtitle_exists(media_path, "en")
            assert result is False
    
    def test_subtitle_exists_iso_639_1(self):
        """Test when subtitle exists with ISO 639-1 code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            srt_path = os.path.join(tmpdir, "video.en.srt")
            
            open(media_path, 'w').close()
            open(srt_path, 'w').close()
            
            result = subtitle_exists(media_path, "en")
            assert result is True
    
    def test_subtitle_exists_different_format(self):
        """Test when subtitle exists with different language format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "video.mp4")
            srt_path = os.path.join(tmpdir, "video.eng.srt")
            
            open(media_path, 'w').close()
            open(srt_path, 'w').close()
            
            # Request with ISO 639-1 but file uses ISO 639-2
            result = subtitle_exists(media_path, "en")
            assert result is True


class TestAddSubgenMarker:
    """Test adding SubGen marker to content."""
    
    def test_adds_marker(self):
        """Test that marker is added."""
        content = "1\n00:00:00,000 --> 00:00:02,000\nTest\n"
        result = add_subgen_marker(content)
        
        assert "SubGen-Azure-Batch" in result
        assert content in result


class TestAppendCreditLine:
    """Test appending credit line to SRT content."""
    
    def test_appends_credit(self):
        """Test that credit line is appended."""
        content = """1
00:00:00,000 --> 00:00:02,000
Hello world
"""
        result = append_credit_line(content)
        
        assert "SubGen-Azure-Batch" in result
        assert "Hello world" in result
    
    def test_empty_content(self):
        """Test with empty content."""
        result = append_credit_line("")
        assert result == ""
    
    def test_credit_timing(self):
        """Test that credit appears after last entry."""
        content = """1
00:00:00,000 --> 00:00:05,000
Test
"""
        result = append_credit_line(content, time_offset=2.0)
        
        # Credit should appear after 5 seconds + 2 second offset = 7 seconds
        assert "00:00:07" in result


class TestWriteLrc:
    """Test writing LRC files."""
    
    def test_write_lrc(self):
        """Test writing an LRC file."""
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:02,000", "Hello"),
            SubtitleEntry(2, "00:01:05,500", "00:01:08,000", "World"),
        ]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_path = os.path.join(tmpdir, "test.lrc")
            
            result = write_lrc(entries, lrc_path)
            
            assert os.path.exists(lrc_path)
            content = open(lrc_path, 'r').read()
            assert "[00:00.00]Hello" in content
            assert "[01:05.50]World" in content
    
    def test_multiline_text_flattened(self):
        """Test that multiline text is flattened."""
        entries = [
            SubtitleEntry(1, "00:00:00,000", "00:00:02,000", "Line 1\nLine 2"),
        ]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            lrc_path = os.path.join(tmpdir, "test.lrc")
            write_lrc(entries, lrc_path)
            
            content = open(lrc_path, 'r').read()
            assert "Line 1 Line 2" in content
            assert "\nLine 2" not in content


class TestGetLrcPath:
    """Test LRC path generation."""
    
    def test_basic_path(self, mock_settings):
        """Test basic LRC path generation."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_lrc_path("/media/song.mp3", "en")
            assert path == "/media/song.en.lrc"
    
    def test_with_subgen_marker(self, mock_settings):
        """Test LRC path with subgen marker."""
        mock_settings.subtitle_naming.show_subgen_marker = True
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        with patch('app.config.get_settings', return_value=mock_settings):
            path = get_lrc_path("/media/song.mp3", "en")
            assert "subgen" in path
            assert path.endswith(".lrc")


class TestSaveLrc:
    """Test saving LRC files from SRT content."""
    
    def test_save_lrc(self, mock_settings):
        """Test saving an LRC file from SRT content."""
        mock_settings.subtitle_naming.show_subgen_marker = False
        mock_settings.subtitle_naming.naming_type = "ISO_639_1"
        mock_settings.subtitle_naming.language_name_override = ""
        
        content = """1
00:00:00,000 --> 00:00:02,000
Hello world
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_path = os.path.join(tmpdir, "song.mp3")
            
            with patch('app.config.get_settings', return_value=mock_settings):
                lrc_path = save_lrc(content, media_path, "en")
            
            assert os.path.exists(lrc_path)
            assert lrc_path.endswith(".lrc")
            
            lrc_content = open(lrc_path, 'r').read()
            assert "Hello world" in lrc_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
