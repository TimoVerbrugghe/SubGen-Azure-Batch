"""
Audio extraction utilities using FFmpeg.

This module handles extraction of audio from video files for transcription.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_transcode_dir() -> Optional[str]:
    """Get the transcode directory from settings, or None to use system temp."""
    settings = get_settings()
    if settings.transcode_dir:
        # Ensure directory exists
        os.makedirs(settings.transcode_dir, exist_ok=True)
        return settings.transcode_dir
    return None


def make_temp_file(suffix: str) -> str:
    """Create a temp file in the configured transcode directory."""
    transcode_dir = get_transcode_dir()
    fd, path = tempfile.mkstemp(suffix=suffix, dir=transcode_dir)
    os.close(fd)
    return path


def make_temp_dir(prefix: str = "subgen_") -> str:
    """Create a temp directory in the configured transcode directory."""
    transcode_dir = get_transcode_dir()
    return tempfile.mkdtemp(prefix=prefix, dir=transcode_dir)


# Supported video file extensions (from original subgen.py)
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mpg', '.mpeg',
    '.3gp', '.ogv', '.vob', '.rm', '.rmvb', '.ts', '.m4v', '.f4v', '.svq3',
    '.asf', '.m2ts', '.divx', '.xvid'
}

# Supported audio file extensions (from original subgen.py)
AUDIO_EXTENSIONS = {
    '.mp3', '.wav', '.aac', '.flac', '.ogg', '.wma', '.alac', '.m4a', '.opus',
    '.aiff', '.aif', '.pcm', '.ra', '.ram', '.mid', '.midi', '.ape', '.wv',
    '.amr', '.vox', '.tak', '.spx', '.m4b', '.mka'
}

# All supported media extensions
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


def is_video_file(path: str) -> bool:
    """Check if file is a video file."""
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(path: str) -> bool:
    """Check if file is an audio file."""
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def is_media_file(path: str) -> bool:
    """Check if file is a supported media file."""
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


async def get_media_duration(file_path: str) -> float:
    """
    Get the duration of a media file in seconds.
    
    Args:
        file_path: Path to media file.
        
    Returns:
        Duration in seconds.
    """
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-show_entries', 'format=duration',
        '-of', 'csv=p=0',
        file_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"ffprobe failed: {stderr.decode()}")
            return 0.0
        
        duration_str = stdout.decode().strip()
        return float(duration_str) if duration_str else 0.0
        
    except Exception as e:
        logger.error(f"Error getting media duration: {e}")
        return 0.0


async def get_audio_info(file_path: str) -> dict:
    """
    Get audio stream information from a media file.
    
    Args:
        file_path: Path to media file.
        
    Returns:
        Dictionary with audio info (codec, sample_rate, channels, etc.)
    """
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name,sample_rate,channels,bit_rate',
        '-of', 'json',
        file_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"ffprobe failed: {stderr.decode()}")
            return {}
        
        import json
        data = json.loads(stdout.decode())
        streams = data.get('streams', [])
        
        if streams:
            return streams[0]
        return {}
        
    except Exception as e:
        logger.error(f"Error getting audio info: {e}")
        return {}


async def extract_audio(
    video_path: str,
    output_path: Optional[str] = None,
    output_format: str = 'ogg',
    sample_rate: int = 16000,
    mono: bool = True,
    audio_track: int = 0
) -> str:
    """
    Extract audio from a video file.
    
    Args:
        video_path: Path to the video file.
        output_path: Optional output path. If not provided, uses temp file.
        output_format: Output audio format (ogg, wav, mp3). OGG recommended for smaller size.
        sample_rate: Target sample rate in Hz.
        mono: Convert to mono audio.
        audio_track: Which audio track to extract (0-indexed).
        
    Returns:
        Path to the extracted audio file.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    
    # Determine output path
    if output_path is None:
        output_path = make_temp_file(suffix=f'.{output_format}')
    
    # Build ffmpeg command
    # Determine codec based on format
    if output_format == 'wav':
        codec = 'pcm_s16le'
    elif output_format == 'ogg':
        codec = 'libopus'  # Opus in OGG container - much smaller than WAV
    elif output_format == 'mp3':
        codec = 'libmp3lame'
    else:
        codec = 'copy'
    
    cmd = [
        'ffmpeg',
        '-y',  # Overwrite output file
        '-i', video_path,
        '-map', f'0:a:{audio_track}',  # Select audio track
        '-vn',  # No video
        '-acodec', codec,
        '-ar', str(sample_rate),
    ]
    
    # Add bitrate for compressed formats
    if output_format in ('ogg', 'mp3'):
        cmd.extend(['-b:a', '64k'])  # 64kbps is sufficient for speech
    
    if mono:
        cmd.extend(['-ac', '1'])
    
    cmd.append(output_path)
    
    logger.info(f"Extracting audio from {video_path} to {output_path}")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode()
            logger.error(f"FFmpeg failed: {error_msg}")
            raise RuntimeError(f"Audio extraction failed: {error_msg}")
        
        logger.info(f"Audio extracted successfully: {output_path}")
        return output_path
        
    except FileNotFoundError:
        raise RuntimeError("FFmpeg not found. Please install FFmpeg.")


async def prepare_audio_for_transcription(
    media_path: str,
    output_dir: Optional[str] = None,
    target_format: str = 'wav'
) -> Tuple[str, bool]:
    """
    Prepare a media file for transcription.
    
    If it's a video, extracts audio. If it's already audio, 
    optionally converts to target format.
    
    Args:
        media_path: Path to media file (video or audio).
        output_dir: Optional directory for output file.
        target_format: Target audio format.
        
    Returns:
        Tuple of (audio_path, is_temp_file).
        If is_temp_file is True, the caller should delete it after use.
    """
    if not is_media_file(media_path):
        raise ValueError(f"Unsupported media file: {media_path}")
    
    if is_audio_file(media_path):
        # Check if format conversion is needed
        current_format = Path(media_path).suffix.lower().lstrip('.')
        
        if current_format == target_format:
            # No conversion needed
            return media_path, False
        
        # Convert audio format
        if output_dir:
            output_path = os.path.join(
                output_dir,
                Path(media_path).stem + f'.{target_format}'
            )
        else:
            output_path = make_temp_file(suffix=f'.{target_format}')
        
        cmd = [
            'ffmpeg',
            '-y',
            '-i', media_path,
            '-ar', '16000',
            '-ac', '1',
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError("Audio conversion failed")
        
        return output_path, True
    
    else:
        # Extract audio from video
        if output_dir:
            output_path = os.path.join(
                output_dir,
                Path(media_path).stem + f'.{target_format}'
            )
        else:
            output_path = None
        
        extracted_path = await extract_audio(
            media_path,
            output_path=output_path,
            output_format=target_format
        )
        
        return extracted_path, True


# Note: get_subtitle_path was removed - use get_srt_path from subtitle_utils instead


def cleanup_temp_file(path: str) -> None:
    """
    Safely delete a temporary file.
    
    Args:
        path: Path to the file to delete.
    """
    try:
        if os.path.exists(path):
            os.unlink(path)
            logger.debug(f"Cleaned up temp file: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup temp file {path}: {e}")


async def extract_audio_segment(
    input_path: str,
    offset: float = 0.0,
    duration: float = 30.0,
    output_format: str = 'wav',
    sample_rate: int = 16000
) -> str:
    """
    Extract a segment of audio from a media file.
    
    Useful for language detection where we only need a small sample.
    
    Args:
        input_path: Path to the input audio/video file.
        offset: Start time in seconds.
        duration: Length of segment in seconds.
        output_format: Output audio format (wav recommended for Azure).
        sample_rate: Target sample rate in Hz.
        
    Returns:
        Path to the extracted audio segment (temp file).
    """
    output_path = make_temp_file(suffix=f'.{output_format}')
    
    # Determine codec
    if output_format == 'wav':
        codec = 'pcm_s16le'
    elif output_format == 'ogg':
        codec = 'libopus'
    else:
        codec = 'pcm_s16le'  # Default to WAV for compatibility
    
    cmd = [
        'ffmpeg',
        '-y',
        '-ss', str(offset),      # Start time
        '-i', input_path,
        '-t', str(duration),     # Duration
        '-vn',                   # No video
        '-acodec', codec,
        '-ar', str(sample_rate),
        '-ac', '1',              # Mono
        output_path
    ]
    
    logger.debug(f"Extracting audio segment: offset={offset}s, duration={duration}s")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode()
            logger.error(f"FFmpeg segment extraction failed: {error_msg}")
            # Try to cleanup failed output
            cleanup_temp_file(output_path)
            raise RuntimeError(f"Audio segment extraction failed: {error_msg}")
        
        logger.debug(f"Audio segment extracted: {output_path}")
        return output_path
        
    except FileNotFoundError:
        cleanup_temp_file(output_path)
        raise RuntimeError("FFmpeg not found. Please install FFmpeg.")


async def get_audio_tracks(video_path: str) -> list:
    """
    Get information about all audio tracks in a video file.
    
    Based on original subgen.py get_audio_tracks() function.
    
    Args:
        video_path: Path to the video file.
        
    Returns:
        List of dictionaries with audio track information:
        - index: Stream index
        - codec: Codec name
        - channels: Number of channels
        - language: Language code (e.g., 'eng', 'deu')
        - title: Track title
        - default: Whether it's the default track
    """
    import json
    
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-select_streams', 'a',
        '-show_entries', 'stream=index,codec_name,channels:stream_tags=language,title,handler_name',
        '-show_entries', 'stream_disposition=default',
        '-of', 'json',
        video_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"ffprobe failed: {stderr.decode()}")
            return []
        
        data = json.loads(stdout.decode())
        streams = data.get('streams', [])
        
        tracks = []
        for i, stream in enumerate(streams):
            tags = stream.get('tags', {})
            disposition = stream.get('disposition', {})
            
            track = {
                'index': i,  # Audio track index (0-based for FFmpeg -map 0:a:X)
                'stream_index': stream.get('index', i),
                'codec': stream.get('codec_name', 'unknown'),
                'channels': stream.get('channels', 2),
                'language': tags.get('language', 'und'),
                'title': tags.get('title', tags.get('handler_name', '')),
                'default': disposition.get('default', 0) == 1
            }
            tracks.append(track)
        
        logger.debug(f"Found {len(tracks)} audio tracks in {video_path}")
        return tracks
        
    except Exception as e:
        logger.error(f"Error getting audio tracks: {e}")
        return []


def find_preferred_audio_track(
    tracks: list,
    preferred_languages: list
) -> tuple:
    """
    Find the best audio track index based on preferred languages.
    
    Based on original subgen.py find_language_audio_track().
    
    Args:
        tracks: List of audio track dictionaries from get_audio_tracks().
        preferred_languages: List of preferred language codes (e.g., ['eng', 'en', 'deu', 'de']).
        
    Returns:
        Tuple of (track_index, language_code) or (0, None) if no preferred language found.
    """
    if not tracks:
        return 0, None
    
    # Normalize preferred languages to lowercase
    preferred = [lang.lower() for lang in preferred_languages]
    
    # First pass: look for exact match in order of preference
    for pref_lang in preferred:
        for track in tracks:
            track_lang = track.get('language', '').lower()
            if track_lang == pref_lang:
                logger.debug(f"Found preferred audio track {track['index']} with language '{track_lang}'")
                return track['index'], track_lang
    
    # Second pass: look for partial match (e.g., 'en' matches 'eng')
    for pref_lang in preferred:
        for track in tracks:
            track_lang = track.get('language', '').lower()
            if track_lang.startswith(pref_lang) or pref_lang.startswith(track_lang):
                logger.debug(f"Found audio track {track['index']} with partial match '{track_lang}' for '{pref_lang}'")
                return track['index'], track_lang
    
    # No match found - return default track (first one)
    logger.debug("No preferred language audio track found, using track 0")
    return 0, tracks[0].get('language') if tracks else None


def has_preferred_audio_language(
    tracks: list,
    preferred_languages: list
) -> bool:
    """
    Check if any audio track matches preferred languages.
    
    Used for LIMIT_TO_PREFERRED_AUDIO_LANGUAGE skip check.
    
    Args:
        tracks: List of audio track dictionaries.
        preferred_languages: List of preferred language codes.
        
    Returns:
        True if at least one track matches a preferred language.
    """
    if not tracks or not preferred_languages:
        return True  # Don't skip if we can't determine
    
    preferred = [lang.lower() for lang in preferred_languages]
    
    for track in tracks:
        track_lang = track.get('language', '').lower()
        for pref_lang in preferred:
            if track_lang == pref_lang or track_lang.startswith(pref_lang) or pref_lang.startswith(track_lang):
                return True
    
    return False
