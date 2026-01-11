"""
Skip checker utilities for SubGen-Azure-Batch.

This module provides functions to determine if subtitle generation
should be skipped for a given media file based on skip configuration.

Based on original subgen.py should_skip_file() logic but adapted for
async/FFprobe-based inspection (no pyav dependency).
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import SkipConfig, get_settings
from app.language_code import LanguageCode
from app.subtitle_utils import SUBTITLE_EXTENSIONS

logger = logging.getLogger(__name__)


@dataclass
class SkipResult:
    """Result of skip check."""
    should_skip: bool
    reason: Optional[str] = None
    
    @classmethod
    def skip(cls, reason: str) -> 'SkipResult':
        """Create a skip result with a reason."""
        return cls(should_skip=True, reason=reason)
    
    @classmethod
    def proceed(cls) -> 'SkipResult':
        """Create a proceed (don't skip) result."""
        return cls(should_skip=False, reason=None)


async def get_stream_info(media_path: str) -> dict:
    """
    Get stream information from a media file using FFprobe.
    
    Args:
        media_path: Path to the media file.
        
    Returns:
        Dictionary with 'audio' and 'subtitle' stream lists.
    """
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        media_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"ffprobe failed for {media_path}: {stderr.decode()}")
            return {'audio': [], 'subtitle': []}
        
        data = json.loads(stdout.decode())
        streams = data.get('streams', [])
        
        audio_streams = []
        subtitle_streams = []
        
        for stream in streams:
            codec_type = stream.get('codec_type', '')
            tags = stream.get('tags', {})
            language = tags.get('language', '').lower()
            
            if codec_type == 'audio':
                audio_streams.append({
                    'index': stream.get('index'),
                    'codec': stream.get('codec_name'),
                    'language': language,
                    'channels': stream.get('channels'),
                })
            elif codec_type == 'subtitle':
                subtitle_streams.append({
                    'index': stream.get('index'),
                    'codec': stream.get('codec_name'),
                    'language': language,
                    'title': tags.get('title', ''),
                })
        
        return {
            'audio': audio_streams,
            'subtitle': subtitle_streams
        }
        
    except Exception as e:
        logger.error(f"Error getting stream info: {e}")
        return {'audio': [], 'subtitle': []}


def get_audio_languages(stream_info: dict) -> List[str]:
    """
    Extract audio language codes from stream info.
    
    Args:
        stream_info: Result from get_stream_info().
        
    Returns:
        List of language codes (lowercase).
    """
    return [
        s['language'] for s in stream_info.get('audio', [])
        if s.get('language')
    ]


def get_internal_subtitle_languages(stream_info: dict) -> List[str]:
    """
    Extract internal subtitle language codes from stream info.
    
    Args:
        stream_info: Result from get_stream_info().
        
    Returns:
        List of language codes (lowercase).
    """
    return [
        s['language'] for s in stream_info.get('subtitle', [])
        if s.get('language')
    ]


def find_external_subtitles(media_path: str) -> List[Tuple[str, str, bool]]:
    """
    Find external subtitle files for a media file.
    
    Args:
        media_path: Path to the media file.
        
    Returns:
        List of tuples: (subtitle_path, language_code, is_subgen)
    """
    media_dir = os.path.dirname(media_path)
    media_name = os.path.splitext(os.path.basename(media_path))[0]
    
    subtitles = []
    
    if not os.path.exists(media_dir):
        return subtitles
    
    for filename in os.listdir(media_dir):
        file_path = os.path.join(media_dir, filename)
        
        # Skip directories
        if os.path.isdir(file_path):
            continue
        
        # Check if it's a subtitle file
        _, ext = os.path.splitext(filename)
        if ext.lower() not in SUBTITLE_EXTENSIONS:
            continue
        
        # Check if it belongs to this media file
        if not filename.startswith(media_name):
            continue
        
        # Extract parts after video name
        subtitle_name = filename[:filename.rfind('.')]  # Remove extension
        parts = subtitle_name[len(media_name):].lstrip('.').split('.')
        
        # Determine if it's a subgen subtitle
        is_subgen = 'subgen' in [p.lower() for p in parts]
        
        # Try to extract language from parts
        language = 'unknown'
        for part in parts:
            if part.lower() == 'subgen':
                continue
            # Try to match as language code
            try:
                lang_code = LanguageCode.from_string(part)
                if lang_code != LanguageCode.NONE:
                    language = lang_code.iso_639_1 or lang_code.iso_639_2_b or part
                    break
            except (ValueError, AttributeError):
                pass
            # Could be a 2 or 3 letter language code
            if len(part) in (2, 3) and part.isalpha():
                language = part.lower()
                break
        
        subtitles.append((file_path, language, is_subgen))
    
    return subtitles


def has_external_subtitle_for_language(
    media_path: str,
    language: str,
    only_subgen: bool = False
) -> bool:
    """
    Check if an external subtitle exists for a specific language.
    
    Args:
        media_path: Path to the media file.
        language: Language code to check (2 or 3 letter).
        only_subgen: Only consider subtitles created by SubGen.
        
    Returns:
        True if matching subtitle exists.
    """
    # Normalize the target language
    try:
        target_lang = LanguageCode.from_string(language)
    except (ValueError, AttributeError):
        target_lang = None
    
    for sub_path, sub_lang, is_subgen in find_external_subtitles(media_path):
        # If only checking subgen subtitles, skip non-subgen
        if only_subgen and not is_subgen:
            continue
        
        # Try to match language codes
        try:
            sub_lang_code = LanguageCode.from_string(sub_lang)
            if target_lang and sub_lang_code == target_lang:
                return True
        except (ValueError, AttributeError):
            pass
        
        # Direct string comparison as fallback
        if sub_lang.lower() == language.lower():
            return True
        
    return False


def has_any_external_subtitle(media_path: str, only_subgen: bool = False) -> bool:
    """
    Check if any external subtitle exists for a media file.
    
    Args:
        media_path: Path to the media file.
        only_subgen: Only consider subtitles created by SubGen.
        
    Returns:
        True if any subtitle exists.
    """
    subtitles = find_external_subtitles(media_path)
    if only_subgen:
        return any(is_subgen for _, _, is_subgen in subtitles)
    return len(subtitles) > 0


def has_internal_subtitle_for_language(stream_info: dict, language: str) -> bool:
    """
    Check if internal subtitles exist for a specific language.
    
    Args:
        stream_info: Result from get_stream_info().
        language: Language code to check.
        
    Returns:
        True if matching internal subtitle exists.
    """
    internal_langs = get_internal_subtitle_languages(stream_info)
    
    # Try to normalize the target language
    try:
        target_lang = LanguageCode.from_string(language)
        if target_lang != LanguageCode.NONE:
            # Check against all internal subtitle languages
            for sub_lang in internal_langs:
                try:
                    sub_lang_code = LanguageCode.from_string(sub_lang)
                    if sub_lang_code == target_lang:
                        return True
                except (ValueError, AttributeError):
                    pass
    except (ValueError, AttributeError):
        pass
    
    # Direct string comparison fallback
    return language.lower() in internal_langs


def audio_matches_skip_languages(stream_info: dict, skip_languages: List[str]) -> bool:
    """
    Check if any audio track language matches the skip list.
    
    Args:
        stream_info: Result from get_stream_info().
        skip_languages: List of language codes to skip.
        
    Returns:
        True if any audio track matches skip list.
    """
    if not skip_languages:
        return False
    
    audio_langs = get_audio_languages(stream_info)
    
    for audio_lang in audio_langs:
        # Direct match
        if audio_lang in skip_languages:
            return True
        
        # Try LanguageCode matching
        try:
            audio_lang_code = LanguageCode.from_string(audio_lang)
            if audio_lang_code != LanguageCode.NONE:
                for skip_lang in skip_languages:
                    try:
                        skip_lang_code = LanguageCode.from_string(skip_lang)
                        if audio_lang_code == skip_lang_code:
                            return True
                    except (ValueError, AttributeError):
                        pass
        except (ValueError, AttributeError):
            pass
    
    return False


def get_all_subtitle_languages(
    media_path: str,
    stream_info: Optional[dict] = None
) -> List[str]:
    """
    Get all subtitle languages for a media file (both internal and external).
    
    Args:
        media_path: Path to the media file.
        stream_info: Optional pre-fetched stream info.
        
    Returns:
        List of language codes (lowercase).
    """
    languages = []
    
    # Get internal subtitle languages from stream info
    if stream_info:
        languages.extend(get_internal_subtitle_languages(stream_info))
    
    # Get external subtitle languages
    for _, lang, _ in find_external_subtitles(media_path):
        if lang and lang != 'unknown':
            languages.append(lang.lower())
    
    return list(set(languages))  # Remove duplicates


def subtitle_matches_skip_languages(
    all_subtitle_langs: List[str],
    skip_languages: List[str]
) -> Optional[str]:
    """
    Check if any subtitle language matches the skip list.
    
    Args:
        all_subtitle_langs: List of all subtitle language codes.
        skip_languages: List of language codes to skip.
        
    Returns:
        The matched language code if found, None otherwise.
    """
    if not skip_languages:
        return None
    
    for sub_lang in all_subtitle_langs:
        # Direct match
        if sub_lang in skip_languages:
            return sub_lang
        
        # Try LanguageCode matching
        try:
            sub_lang_code = LanguageCode.from_string(sub_lang)
            if sub_lang_code != LanguageCode.NONE:
                for skip_lang in skip_languages:
                    try:
                        skip_lang_code = LanguageCode.from_string(skip_lang)
                        if sub_lang_code == skip_lang_code:
                            return skip_lang
                    except (ValueError, AttributeError):
                        pass
        except (ValueError, AttributeError):
            pass
    
    return None


async def should_skip_file(
    media_path: str,
    target_language: str,
    skip_config: Optional[SkipConfig] = None
) -> SkipResult:
    """
    Determine if subtitle generation should be skipped for a file.
    
    This is the main entry point for skip checking, mirroring the
    original subgen.py should_skip_file() function.
    
    Args:
        media_path: Path to the media file.
        target_language: Target language for transcription.
        skip_config: Skip configuration. If None, uses settings from env.
        
    Returns:
        SkipResult indicating whether to skip and why.
    """
    if skip_config is None:
        skip_config = get_settings().skip
    
    base_name = os.path.basename(media_path)
    
    # Check if file exists
    if not os.path.exists(media_path):
        return SkipResult.skip(f"File not found: {base_name}")
    
    # 1. Skip if target language subtitle already exists
    if skip_config.skip_if_target_subtitles_exist:
        if has_external_subtitle_for_language(
            media_path, 
            target_language, 
            only_subgen=skip_config.skip_only_subgen_subtitles
        ):
            return SkipResult.skip(
                f"Subtitle already exists for language '{target_language}'"
            )
    
    # 2. Skip if any external subtitle exists
    if skip_config.skip_if_external_subtitles_exist:
        if has_any_external_subtitle(
            media_path,
            only_subgen=skip_config.skip_only_subgen_subtitles
        ):
            return SkipResult.skip("External subtitles already exist")
    
    # 3. Stream-based checks (internal subtitles, audio language, subtitle languages)
    # Get stream info once for all checks that need it
    internal_lang = skip_config.internal_subtitle_language
    audio_skip_list = skip_config.audio_language_skip_list
    subtitle_skip_list = skip_config.subtitle_languages_skip_list
    
    needs_stream_info = internal_lang or audio_skip_list or subtitle_skip_list
    stream_info = None
    
    if needs_stream_info:
        stream_info = await get_stream_info(media_path)
        
        # 3a. Check internal subtitles for specific language
        if internal_lang:
            if has_internal_subtitle_for_language(stream_info, internal_lang):
                return SkipResult.skip(
                    f"Internal subtitles exist in '{internal_lang}'"
                )
        
        # 3b. Skip if audio track language matches skip list
        if audio_skip_list:
            if audio_matches_skip_languages(stream_info, audio_skip_list):
                matched_langs = ', '.join(audio_skip_list)
                return SkipResult.skip(
                    f"Audio track language in skip list ({matched_langs})"
                )
        
        # 3c. Skip if any subtitle language is in the skip list (SKIP_SUBTITLE_LANGUAGES)
        if subtitle_skip_list:
            all_sub_langs = get_all_subtitle_languages(media_path, stream_info)
            matched_lang = subtitle_matches_skip_languages(all_sub_langs, subtitle_skip_list)
            if matched_lang:
                return SkipResult.skip(
                    f"Contains subtitle in skip list language '{matched_lang}'"
                )
        
        # 3d. Skip if audio has unknown/undefined language (SKIP_UNKNOWN_LANGUAGE)
        if skip_config.skip_unknown_language:
            audio_streams = stream_info.get('audio', [])
            has_unknown_audio = any(
                not stream.get('language') or stream.get('language') in ('', 'und', 'unknown')
                for stream in audio_streams
            )
            if has_unknown_audio:
                return SkipResult.skip("Audio track has unknown/undefined language")
        
        # 3e. Skip if no language is set but subtitles exist (SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST)
        if skip_config.skip_if_no_language_but_subtitles_exist:
            audio_streams = stream_info.get('audio', [])
            has_language = any(
                stream.get('language') and stream.get('language') not in ('', 'und', 'unknown')
                for stream in audio_streams
            )
            if not has_language:
                # No audio language set - check if subtitles exist
                subtitle_streams = stream_info.get('subtitle', [])
                external_subs = find_external_subtitles(media_path)
                if subtitle_streams or external_subs:
                    return SkipResult.skip(
                        "No audio language set but subtitles already exist"
                    )
    
    # 4. Check preferred audio language (LIMIT_TO_PREFERRED_AUDIO_LANGUAGE)
    settings = get_settings()
    if settings.transcription.limit_to_preferred_audio_languages:
        preferred_langs = settings.transcription.preferred_audio_languages_list
        if preferred_langs:
            from app.audio_extractor import (get_audio_tracks,
                                             has_preferred_audio_language)
            audio_tracks = await get_audio_tracks(media_path)
            if audio_tracks and not has_preferred_audio_language(audio_tracks, preferred_langs):
                preferred_names = ', '.join(preferred_langs)
                return SkipResult.skip(
                    f"No audio track in preferred languages ({preferred_names})"
                )
    
    # No skip conditions met
    logger.debug(f"No skip conditions met for {base_name}")
    return SkipResult.proceed()


async def check_batch_files(
    file_paths: List[str],
    target_language: str,
    skip_config: Optional[SkipConfig] = None
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Check multiple files for skip conditions.
    
    Args:
        file_paths: List of media file paths.
        target_language: Target language for transcription.
        skip_config: Skip configuration.
        
    Returns:
        Tuple of (files_to_process, skipped_files_with_reasons)
    """
    files_to_process = []
    skipped_files = []
    
    for file_path in file_paths:
        result = await should_skip_file(file_path, target_language, skip_config)
        if result.should_skip:
            skipped_files.append((file_path, result.reason or "Unknown reason"))
            logger.info(f"Skipping {os.path.basename(file_path)}: {result.reason}")
        else:
            files_to_process.append(file_path)
    
    return files_to_process, skipped_files
