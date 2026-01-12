"""
Utility modules for SubGen-Azure-Batch.

This package contains utility functions and clients used by the TranscriptionService:
- audio_extractor: FFmpeg audio extraction utilities
- azure_batch_transcriber: Azure Speech Services Batch API client
- bazarr_client: Bazarr API integration
- language_code: ISO 639 language code definitions
- media_server_client: Plex/Jellyfin/Emby API clients
- notification_service: Pushover notifications
- skip_checker: Skip logic for existing subtitles
- subtitle_utils: SRT/LRC file utilities
"""

# Re-export commonly used items for convenience
from app.utils.audio_extractor import (AUDIO_EXTENSIONS, MEDIA_EXTENSIONS,
                                       VIDEO_EXTENSIONS, extract_audio,
                                       is_audio_file, is_media_file,
                                       is_video_file, make_temp_dir,
                                       make_temp_file)
from app.utils.azure_batch_transcriber import (AzureBatchTranscriber,
                                               TranscriptionJob,
                                               TranscriptionResult,
                                               TranscriptionSegment,
                                               TranscriptionStatus)
from app.utils.bazarr_client import BazarrClient, notify_bazarr_of_new_subtitle
from app.utils.language_code import LanguageCode
from app.utils.media_server_client import (JellyfinClient, PlexClient,
                                           refresh_all_configured_servers,
                                           refresh_by_file_path)
from app.utils.notification_service import NotificationService, notify_failure
from app.utils.skip_checker import SkipResult, should_skip_file
from app.utils.subtitle_utils import (SUBTITLE_EXTENSIONS, append_credit_line,
                                      get_srt_path, save_lrc, save_srt,
                                      seconds_to_srt_time)
