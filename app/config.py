"""
Configuration management for SubGen-Azure-Batch.

Loads configuration from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

# Version - read from environment (set in Dockerfile) or default
SUBGEN_AZURE_BATCH_VERSION = os.getenv("SUBGEN_AZURE_BATCH_VERSION", "1.0.0")

def get_bool(value: str) -> bool:
    """Convert string to boolean."""
    return str(value).lower() in ('true', 'on', '1', 'yes')


def get_list(value: str, separator: str = ',') -> List[str]:
    """Convert comma-separated string to list."""
    if not value:
        return []
    return [item.strip() for item in value.split(separator) if item.strip()]


@dataclass
class AzureConfig:
    """Azure Speech Services configuration."""
    speech_key: str = ""
    speech_region: str = "swedencentral"
    storage_connection_string: str = ""
    storage_container: str = "transcription-audio"
    
    @property
    def is_configured(self) -> bool:
        """Check if Azure is properly configured."""
        return bool(self.speech_key and self.speech_region)
    
    @property
    def requires_storage(self) -> bool:
        """Check if storage is configured (required for batch transcription)."""
        return bool(self.storage_connection_string)
    
    @property
    def api_base_url(self) -> str:
        """Get the Azure Speech API base URL."""
        return f"https://{self.speech_region}.api.cognitive.microsoft.com/speechtotext/v3.2"


@dataclass
class BazarrConfig:
    """Bazarr integration configuration."""
    url: str = ""
    api_key: str = ""
    
    @property
    def is_configured(self) -> bool:
        """Check if Bazarr is configured."""
        return bool(self.url and self.api_key)


@dataclass
class PlexConfig:
    """Plex integration configuration."""
    token: str = ""
    server: str = ""
    
    @property
    def is_configured(self) -> bool:
        """Check if Plex is configured."""
        return bool(self.token and self.server)


@dataclass
class JellyfinConfig:
    """Jellyfin integration configuration."""
    token: str = ""
    server: str = ""
    
    @property
    def is_configured(self) -> bool:
        """Check if Jellyfin is configured."""
        return bool(self.token and self.server)


@dataclass
class EmbyConfig:
    """Emby integration configuration."""
    token: str = ""
    server: str = ""
    
    @property
    def is_configured(self) -> bool:
        """Check if Emby is configured."""
        return bool(self.token and self.server)


@dataclass
class PathMappingConfig:
    """Path mapping configuration for Docker volume differences."""
    enabled: bool = False
    from_path: str = ""
    to_path: str = ""
    
    def apply(self, path: str) -> str:
        """Apply path mapping if enabled."""
        if self.enabled and self.from_path:
            return path.replace(self.from_path, self.to_path)
        return path


@dataclass 
class ProcessingConfig:
    """Processing control configuration."""
    process_added_media: bool = False  # Process on library.new events
    process_on_play: bool = False  # Process on media.play events


@dataclass
class TranscriptionConfig:
    """Transcription behavior configuration.
    
    Controls transcription output and language handling.
    """
    # Force all language detection to return this language (e.g., 'en', 'es')
    # Empty string means auto-detect. Based on FORCE_DETECTED_LANGUAGE_TO.
    force_language: str = ""
    
    # Append a credit line at the end of subtitles (e.g., "Transcribed by SubGen-Azure-Batch")
    # Based on original subgen.py APPEND setting.
    append_credit_line: bool = False
    
    # Write LRC files for audio files instead of SRT
    # Based on original subgen.py LRC_FOR_AUDIO_FILES setting.
    lrc_for_audio_files: bool = True
    
    # Preferred audio languages for audio track selection (pipe-separated, e.g., "eng|deu")
    # Based on original subgen.py PREFERRED_AUDIO_LANGUAGES setting.
    preferred_audio_languages: str = ""
    
    # Limit transcription to files with audio tracks in preferred languages
    # Based on original subgen.py LIMIT_TO_PREFERRED_AUDIO_LANGUAGE setting.
    limit_to_preferred_audio_languages: bool = False
    
    # Detect language on the first x seconds of audio (default: 30)
    # Based on original subgen.py DETECT_LANGUAGE_LENGTH setting.
    detect_language_length: int = 30
    
    # Start language detection x seconds into the file (to skip intros/songs)
    # Based on original subgen.py DETECT_LANGUAGE_OFFSET setting.
    detect_language_offset: int = 0
    
    # Candidate locales for Azure language identification (max 4 for Single mode)
    # Used by /detect-language endpoint for Bazarr integration
    language_detection_candidates: str = "en-US,nl-NL,es-ES,fr-FR"
    
    @property
    def forced_language(self) -> Optional[str]:
        """Get the forced language, or None if not set."""
        return self.force_language.strip() or None
    
    @property
    def preferred_audio_languages_list(self) -> list:
        """Get the list of preferred audio languages."""
        if not self.preferred_audio_languages.strip():
            return []
        return [lang.strip().lower() for lang in self.preferred_audio_languages.split('|') if lang.strip()]


@dataclass
class SubtitleNamingConfig:
    """Subtitle file naming configuration.
    
    Controls how language codes appear in subtitle filenames.
    Based on original subgen.py SUBTITLE_LANGUAGE_NAMING_TYPE.
    
    Examples for Spanish:
        - ISO_639_1: "es"
        - ISO_639_2_T: "spa" (terminology)
        - ISO_639_2_B: "spa" (bibliographic)
        - NAME: "Spanish"
        - NATIVE: "Español"
    """
    # The naming format type - must be uppercase
    naming_type: str = "ISO_639_2_B"
    
    # Include '.subgen' marker in filename
    show_subgen_marker: bool = False
    
    # Override the detected language in subtitle filename (e.g., 'aa' to sort higher in Plex)
    # Empty string means use detected language. Based on SUBTITLE_LANGUAGE_NAME.
    subtitle_language_name: str = ""
    
    @property
    def valid_types(self) -> tuple:
        """Return valid naming type values."""
        return ("ISO_639_1", "ISO_639_2_T", "ISO_639_2_B", "NAME", "NATIVE")
    
    @property
    def is_valid(self) -> bool:
        """Check if naming type is valid."""
        return self.naming_type.upper() in self.valid_types
    
    @property
    def language_name_override(self) -> Optional[str]:
        """Get the language name override, or None if not set."""
        return self.subtitle_language_name.strip() or None


@dataclass
class SkipConfig:
    """Skip configuration - determines when to skip subtitle generation.
    
    Based on original subgen.py skip logic but adapted for cloud-based processing.
    """
    # Skip if target language subtitle already exists (internal or external)
    # Based on SKIP_IF_TARGET_SUBTITLES_EXIST
    skip_if_target_subtitles_exist: bool = True
    
    # Skip if any external subtitle file exists (any language)
    skip_if_external_subtitles_exist: bool = False
    
    # Skip if internal subtitles exist in this language (e.g., 'en', 'eng')
    # Empty string means don't check internal subtitles
    skip_if_internal_subtitles_language: str = ""
    
    # Only skip if the existing subtitle was created by SubGen
    # (checks for '.subgen.' in filename)
    skip_only_subgen_subtitles: bool = False
    
    # Skip files where audio track is in one of these languages (pipe-separated, e.g., 'en|eng|english')
    # Useful to skip English audio files when you only want to transcribe foreign content
    # Based on SKIP_IF_AUDIO_TRACK_IS
    skip_if_audio_track_is: str = ""
    
    # Skip if video already has subtitles in any of these languages (pipe-separated, e.g., 'en|es|fr')
    # Checks both internal and external subtitles. Based on SKIP_SUBTITLE_LANGUAGES.
    skip_subtitle_languages: str = ""
    
    # Skip if the audio track has an unknown/undefined language
    # Based on SKIP_UNKNOWN_LANGUAGE
    skip_unknown_language: bool = False
    
    # Skip if no audio language is set but subtitles already exist
    # Based on SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST
    skip_if_no_language_but_subtitles_exist: bool = False
    
    @property
    def internal_subtitle_language(self) -> Optional[str]:
        """Get the internal subtitle language to check, or None if not set."""
        return self.skip_if_internal_subtitles_language.strip() or None
    
    @property
    def audio_language_skip_list(self) -> List[str]:
        """Get list of audio languages to skip."""
        if not self.skip_if_audio_track_is.strip():
            return []
        return [lang.strip().lower() for lang in self.skip_if_audio_track_is.split('|') if lang.strip()]
    
    @property
    def subtitle_languages_skip_list(self) -> List[str]:
        """Get list of subtitle languages that trigger a skip."""
        if not self.skip_subtitle_languages.strip():
            return []
        return [lang.strip().lower() for lang in self.skip_subtitle_languages.split('|') if lang.strip()]


@dataclass
class Settings:
    """Application settings."""
    
    # Server settings
    debug: bool = False
    
    # UI settings
    default_theme: str = "dark"  # 'dark' or 'light'
    
    # Media settings
    media_folders: List[str] = field(default_factory=list)
    subtitle_language: str = ""
    
    # Processing settings
    concurrent_transcriptions: int = 50
    job_poll_interval: int = 10  # seconds
    audio_format: str = "wav"  # Format for extracted audio
    transcode_dir: str = "/transcode"  # Directory for temp audio files
    
    # Azure configuration
    azure: AzureConfig = field(default_factory=AzureConfig)
    
    # Path mapping (for Docker volume differences)
    path_mapping: PathMappingConfig = field(default_factory=PathMappingConfig)
    
    # Processing control
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    
    # Skip configuration
    skip: SkipConfig = field(default_factory=SkipConfig)
    
    # Subtitle naming configuration
    subtitle_naming: SubtitleNamingConfig = field(default_factory=SubtitleNamingConfig)
    
    # Transcription configuration
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    
    # Integration configurations
    bazarr: BazarrConfig = field(default_factory=BazarrConfig)
    plex: PlexConfig = field(default_factory=PlexConfig)
    jellyfin: JellyfinConfig = field(default_factory=JellyfinConfig)
    emby: EmbyConfig = field(default_factory=EmbyConfig)
    
    @classmethod
    def from_env(cls) -> 'Settings':
        """Load settings from environment variables."""
        return cls(
            # Server settings
            debug=get_bool(os.getenv('DEBUG', 'false')),
            
            # UI settings
            default_theme=os.getenv('DEFAULT_THEME', 'dark'),
            
            # Media settings
            media_folders=get_list(os.getenv('MEDIA_FOLDERS', '/tv,/movies')),
            subtitle_language=os.getenv('SUBTITLE_LANGUAGE', ''),
            
            # Processing settings
            concurrent_transcriptions=int(os.getenv('CONCURRENT_TRANSCRIPTIONS', '50')),
            job_poll_interval=int(os.getenv('JOB_POLL_INTERVAL', '10')),
            audio_format=os.getenv('AUDIO_FORMAT', 'wav'),
            transcode_dir=os.getenv('TRANSCODE_DIR', '/transcode'),
            
            # Azure configuration
            azure=AzureConfig(
                speech_key=os.getenv('AZURE_SPEECH_KEY', ''),
                speech_region=os.getenv('AZURE_SPEECH_REGION', 'swedencentral'),
                storage_connection_string=os.getenv('AZURE_STORAGE_CONNECTION_STRING', ''),
                storage_container=os.getenv('AZURE_STORAGE_CONTAINER', 'transcription-audio'),
            ),
            
            # Path mapping configuration
            path_mapping=PathMappingConfig(
                enabled=get_bool(os.getenv('USE_PATH_MAPPING', 'false')),
                from_path=os.getenv('PATH_MAPPING_FROM', ''),
                to_path=os.getenv('PATH_MAPPING_TO', ''),
            ),
            
            # Processing control
            processing=ProcessingConfig(
                process_added_media=get_bool(os.getenv('PROCESS_ADDED_MEDIA', 'false')),
                process_on_play=get_bool(os.getenv('PROCESS_MEDIA_ON_PLAY', 'false')),
            ),
            
            # Skip configuration
            skip=SkipConfig(
                skip_if_target_subtitles_exist=get_bool(os.getenv('SKIP_IF_TARGET_SUBTITLES_EXIST', 'true')),
                skip_if_external_subtitles_exist=get_bool(os.getenv('SKIP_IF_EXTERNAL_SUBTITLES_EXIST', 'false')),
                skip_if_internal_subtitles_language=os.getenv('SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE', ''),
                skip_only_subgen_subtitles=get_bool(os.getenv('SKIP_ONLY_SUBGEN_SUBTITLES', 'false')),
                skip_if_audio_track_is=os.getenv('SKIP_IF_AUDIO_TRACK_IS', ''),
                skip_subtitle_languages=os.getenv('SKIP_SUBTITLE_LANGUAGES', ''),
                skip_unknown_language=get_bool(os.getenv('SKIP_UNKNOWN_LANGUAGE', 'false')),
                skip_if_no_language_but_subtitles_exist=get_bool(os.getenv('SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST', 'false')),
            ),
            
            # Subtitle naming configuration
            subtitle_naming=SubtitleNamingConfig(
                naming_type=os.getenv('SUBTITLE_LANGUAGE_NAMING_TYPE', 'ISO_639_2_B').upper(),
                show_subgen_marker=get_bool(os.getenv('SHOW_IN_SUBNAME_SUBGEN', 'false')),
                subtitle_language_name=os.getenv('SUBTITLE_LANGUAGE_NAME', ''),
            ),
            
            # Transcription configuration
            transcription=TranscriptionConfig(
                force_language=os.getenv('FORCE_DETECTED_LANGUAGE_TO', ''),
                append_credit_line=get_bool(os.getenv('APPEND', 'false')),
                lrc_for_audio_files=get_bool(os.getenv('LRC_FOR_AUDIO_FILES', 'true')),
                preferred_audio_languages=os.getenv('PREFERRED_AUDIO_LANGUAGES', ''),
                limit_to_preferred_audio_languages=get_bool(os.getenv('LIMIT_TO_PREFERRED_AUDIO_LANGUAGE', 'false')),
                detect_language_length=int(os.getenv('DETECT_LANGUAGE_LENGTH', '30')),
                detect_language_offset=int(os.getenv('DETECT_LANGUAGE_OFFSET', '0')),
                language_detection_candidates=os.getenv('LANGUAGE_DETECTION_CANDIDATES', 'en-US,nl-NL,es-ES,fr-FR'),
            ),
            
            # Bazarr configuration
            bazarr=BazarrConfig(
                url=os.getenv('BAZARR_URL', ''),
                api_key=os.getenv('BAZARR_API_KEY', ''),
            ),
            
            # Plex configuration
            plex=PlexConfig(
                token=os.getenv('PLEX_TOKEN', ''),
                server=os.getenv('PLEX_SERVER', ''),
            ),
            
            # Jellyfin configuration
            jellyfin=JellyfinConfig(
                token=os.getenv('JELLYFIN_TOKEN', ''),
                server=os.getenv('JELLYFIN_SERVER', ''),
            ),
            
            # Emby configuration
            emby=EmbyConfig(
                token=os.getenv('EMBY_TOKEN', ''),
                server=os.getenv('EMBY_SERVER', ''),
            ),
        )


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings.from_env()


def require_azure_configured() -> None:
    """
    Check if Azure is configured. Raises HTTPException if not.
    
    Use as a FastAPI dependency or call directly in route handlers.
    
    Raises:
        HTTPException: 503 if Azure Speech Services not configured.
    """
    from fastapi import HTTPException
    settings = get_settings()
    if not settings.azure.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Azure Speech Services not configured. Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
        )


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds.
        
    Returns:
        Formatted string like "3 minutes and 29 seconds" or "45 seconds".
    """
    minutes, secs = divmod(int(seconds), 60)
    if minutes > 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''} and {secs} second{'s' if secs != 1 else ''}"
    return f"{secs} second{'s' if secs != 1 else ''}"


# Export for convenience
settings = get_settings()
