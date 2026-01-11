"""
Tests for configuration module.

Tests cover:
- Configuration dataclasses and their properties
- Environment variable parsing
- Settings singleton behavior
- Validation logic
"""

import os
from unittest.mock import patch

import pytest


class TestGetBool:
    """Test get_bool helper function."""
    
    def test_true_values(self):
        """Test that true-like values return True."""
        from app.config import get_bool
        
        assert get_bool('true') is True
        assert get_bool('True') is True
        assert get_bool('TRUE') is True
        assert get_bool('on') is True
        assert get_bool('ON') is True
        assert get_bool('1') is True
        assert get_bool('yes') is True
        assert get_bool('YES') is True
    
    def test_false_values(self):
        """Test that false-like values return False."""
        from app.config import get_bool
        
        assert get_bool('false') is False
        assert get_bool('False') is False
        assert get_bool('FALSE') is False
        assert get_bool('off') is False
        assert get_bool('0') is False
        assert get_bool('no') is False
        assert get_bool('') is False
        assert get_bool('random') is False


class TestGetList:
    """Test get_list helper function."""
    
    def test_comma_separated(self):
        """Test parsing comma-separated values."""
        from app.config import get_list
        
        assert get_list('a,b,c') == ['a', 'b', 'c']
        assert get_list('one, two, three') == ['one', 'two', 'three']
    
    def test_empty_value(self):
        """Test parsing empty value."""
        from app.config import get_list
        
        assert get_list('') == []
        assert get_list(None) == []
    
    def test_custom_separator(self):
        """Test parsing with custom separator."""
        from app.config import get_list
        
        assert get_list('a|b|c', separator='|') == ['a', 'b', 'c']
    
    def test_strips_whitespace(self):
        """Test that whitespace is stripped from values."""
        from app.config import get_list
        
        result = get_list('  a  ,  b  ,  c  ')
        assert result == ['a', 'b', 'c']


class TestAzureConfig:
    """Test AzureConfig dataclass."""
    
    def test_is_configured_when_key_and_region(self):
        """Test is_configured returns True when key and region are set."""
        from app.config import AzureConfig
        
        config = AzureConfig(
            speech_key="test-key",
            speech_region="swedencentral"
        )
        assert config.is_configured is True
    
    def test_is_configured_when_missing_key(self):
        """Test is_configured returns False when key is missing."""
        from app.config import AzureConfig
        
        config = AzureConfig(
            speech_key="",
            speech_region="swedencentral"
        )
        assert config.is_configured is False
    
    def test_requires_storage(self):
        """Test requires_storage property."""
        from app.config import AzureConfig
        
        config_with_storage = AzureConfig(
            speech_key="key",
            speech_region="region",
            storage_connection_string="connection-string"
        )
        assert config_with_storage.requires_storage is True
        
        config_without = AzureConfig(
            speech_key="key",
            speech_region="region"
        )
        assert config_without.requires_storage is False
    
    def test_api_base_url(self):
        """Test API base URL generation."""
        from app.config import AzureConfig
        
        config = AzureConfig(
            speech_key="key",
            speech_region="swedencentral"
        )
        expected = "https://swedencentral.api.cognitive.microsoft.com/speechtotext/v3.2"
        assert config.api_base_url == expected


class TestBazarrConfig:
    """Test BazarrConfig dataclass."""
    
    def test_is_configured_when_url_and_key(self):
        """Test is_configured returns True when both URL and key are set."""
        from app.config import BazarrConfig
        
        config = BazarrConfig(
            url="http://localhost:6767",
            api_key="test-key"
        )
        assert config.is_configured is True
    
    def test_is_configured_when_missing_url(self):
        """Test is_configured returns False when URL is missing."""
        from app.config import BazarrConfig
        
        config = BazarrConfig(
            url="",
            api_key="test-key"
        )
        assert config.is_configured is False


class TestPlexConfig:
    """Test PlexConfig dataclass."""
    
    def test_is_configured(self):
        """Test is_configured property."""
        from app.config import PlexConfig
        
        configured = PlexConfig(token="token", server="http://localhost:32400")
        assert configured.is_configured is True
        
        not_configured = PlexConfig(token="", server="")
        assert not_configured.is_configured is False


class TestJellyfinConfig:
    """Test JellyfinConfig dataclass."""
    
    def test_is_configured(self):
        """Test is_configured property."""
        from app.config import JellyfinConfig
        
        configured = JellyfinConfig(token="token", server="http://localhost:8096")
        assert configured.is_configured is True


class TestEmbyConfig:
    """Test EmbyConfig dataclass."""
    
    def test_is_configured(self):
        """Test is_configured property."""
        from app.config import EmbyConfig
        
        configured = EmbyConfig(token="token", server="http://localhost:8096")
        assert configured.is_configured is True


class TestPathMappingConfig:
    """Test PathMappingConfig dataclass."""
    
    def test_apply_when_enabled(self):
        """Test path mapping is applied when enabled."""
        from app.config import PathMappingConfig
        
        config = PathMappingConfig(
            enabled=True,
            from_path="/tv",
            to_path="/Volumes/TV"
        )
        
        result = config.apply("/tv/Show/episode.mkv")
        assert result == "/Volumes/TV/Show/episode.mkv"
    
    def test_apply_when_disabled(self):
        """Test path mapping is not applied when disabled."""
        from app.config import PathMappingConfig
        
        config = PathMappingConfig(
            enabled=False,
            from_path="/tv",
            to_path="/Volumes/TV"
        )
        
        result = config.apply("/tv/Show/episode.mkv")
        assert result == "/tv/Show/episode.mkv"


class TestTranscriptionConfig:
    """Test TranscriptionConfig dataclass."""
    
    def test_forced_language_property(self):
        """Test forced_language returns None when empty."""
        from app.config import TranscriptionConfig
        
        config = TranscriptionConfig(force_language="")
        assert config.forced_language is None
        
        config = TranscriptionConfig(force_language="en")
        assert config.forced_language == "en"
    
    def test_preferred_audio_languages_list(self):
        """Test parsing pipe-separated audio languages."""
        from app.config import TranscriptionConfig
        
        config = TranscriptionConfig(preferred_audio_languages="eng|deu|fra")
        assert config.preferred_audio_languages_list == ["eng", "deu", "fra"]
        
        config_empty = TranscriptionConfig(preferred_audio_languages="")
        assert config_empty.preferred_audio_languages_list == []


class TestSubtitleNamingConfig:
    """Test SubtitleNamingConfig dataclass."""
    
    def test_valid_types(self):
        """Test valid naming types."""
        from app.config import SubtitleNamingConfig
        
        config = SubtitleNamingConfig()
        assert "ISO_639_1" in config.valid_types
        assert "ISO_639_2_T" in config.valid_types
        assert "ISO_639_2_B" in config.valid_types
        assert "NAME" in config.valid_types
        assert "NATIVE" in config.valid_types
    
    def test_is_valid(self):
        """Test naming type validation."""
        from app.config import SubtitleNamingConfig
        
        valid_config = SubtitleNamingConfig(naming_type="ISO_639_1")
        assert valid_config.is_valid is True
        
        invalid_config = SubtitleNamingConfig(naming_type="INVALID")
        assert invalid_config.is_valid is False


class TestFormatDuration:
    """Test format_duration helper function."""
    
    def test_seconds_only(self):
        """Test formatting seconds."""
        from app.config import format_duration
        
        assert format_duration(5) == "5 seconds"
        assert format_duration(1) == "1 second"
        assert format_duration(30) == "30 seconds"
    
    def test_minutes_and_seconds(self):
        """Test formatting minutes and seconds."""
        from app.config import format_duration
        
        assert format_duration(65) == "1 minute and 5 seconds"
        assert format_duration(121) == "2 minutes and 1 second"
        assert format_duration(125) == "2 minutes and 5 seconds"
    
    def test_zero_seconds(self):
        """Test formatting zero seconds."""
        from app.config import format_duration
        
        assert format_duration(0) == "0 seconds"
        assert format_duration(60) == "1 minute and 0 seconds"


class TestSettingsSingleton:
    """Test Settings singleton behavior."""
    
    def test_get_settings_returns_same_instance(self):
        """Test that get_settings returns a cached instance."""
        from app.config import get_settings
        
        settings1 = get_settings()
        settings2 = get_settings()
        
        # Same instance (due to lru_cache)
        assert settings1 is settings2
    
    def test_settings_has_required_attributes(self):
        """Test that Settings has all required configuration attributes."""
        from app.config import get_settings
        
        settings = get_settings()
        
        # Check all config sections exist
        assert hasattr(settings, 'azure')
        assert hasattr(settings, 'bazarr')
        assert hasattr(settings, 'plex')
        assert hasattr(settings, 'jellyfin')
        assert hasattr(settings, 'emby')
        assert hasattr(settings, 'transcription')
        assert hasattr(settings, 'skip')
        assert hasattr(settings, 'subtitle_naming')
        assert hasattr(settings, 'path_mapping')


class TestRequireAzureConfigured:
    """Test require_azure_configured function."""
    
    def test_raises_when_not_configured(self):
        """Test that HTTPException is raised when Azure is not configured."""
        # Create unconfigured settings
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from app.config import AzureConfig, require_azure_configured
        mock_settings = MagicMock()
        mock_settings.azure = AzureConfig(speech_key="", speech_region="")
        
        with patch('app.config.get_settings', return_value=mock_settings):
            with pytest.raises(HTTPException) as exc_info:
                require_azure_configured()
            
            assert exc_info.value.status_code == 503
            assert "not configured" in exc_info.value.detail.lower()
    
    def test_passes_when_configured(self, mock_settings):
        """Test that no exception is raised when Azure is configured."""
        from app.config import require_azure_configured
        
        with patch('app.config.get_settings', return_value=mock_settings):
            # Should not raise
            require_azure_configured()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
