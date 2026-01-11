"""
Subtitle utilities for SubGen-Azure-Batch.

This module provides utilities for working with SRT subtitle files,
including saving, loading, and merging subtitles.
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from app.language_code import LanguageCode

logger = logging.getLogger(__name__)

# Subtitle file extensions (canonical location - import from here)
SUBTITLE_EXTENSIONS = {'.srt', '.vtt', '.sub', '.ass', '.ssa', '.idx', '.sbv', '.pgs', '.ttml', '.lrc'}


def format_language_for_filename(language: str) -> str:
    """
    Format a language code for use in subtitle filenames.
    
    Uses SUBTITLE_LANGUAGE_NAME override if set, otherwise formats according
    to SUBTITLE_LANGUAGE_NAMING_TYPE from config.
    Based on original subgen.py define_subtitle_language_naming().
    
    Args:
        language: Language code (ISO 639-1, ISO 639-2, or name).
        
    Returns:
        Formatted language string based on naming type setting,
        or the override value from SUBTITLE_LANGUAGE_NAME if set.
        
    Examples (for Spanish with different naming types):
        - ISO_639_1: "es"
        - ISO_639_2_T: "spa"
        - ISO_639_2_B: "spa"
        - NAME: "Spanish"
        - NATIVE: "Español"
    """
    from app.config import get_settings
    settings = get_settings()
    
    # Check for override first (SUBTITLE_LANGUAGE_NAME)
    # This allows users to set a custom language code like 'aa' to sort higher in Plex
    override = settings.subtitle_naming.language_name_override
    if override:
        return override
    
    naming_type = settings.subtitle_naming.naming_type.upper()
    
    # Parse the language string to a LanguageCode enum
    lang_code = LanguageCode.from_string(language)
    
    # If we couldn't parse it, return as-is
    if lang_code == LanguageCode.NONE:
        logger.warning(f"Unknown language code '{language}', using as-is for filename")
        return language
    
    # Map naming type to the appropriate method
    format_map = {
        "ISO_639_1": lambda: lang_code.to_iso_639_1(),
        "ISO_639_2_T": lambda: lang_code.to_iso_639_2_t(),
        "ISO_639_2_B": lambda: lang_code.to_iso_639_2_b(),
        "NAME": lambda: lang_code.to_name(in_english=True),
        "NATIVE": lambda: lang_code.to_name(in_english=False),
    }
    
    # Get the formatter, default to ISO_639_2_B
    formatter = format_map.get(naming_type, format_map["ISO_639_2_B"])
    result = formatter()
    
    # Fallback if the result is None
    if result is None:
        logger.warning(f"No {naming_type} code for '{language}', falling back to ISO 639-1")
        result = lang_code.to_iso_639_1() or language
    
    return result


@dataclass
class SubtitleEntry:
    """A single subtitle entry."""
    index: int
    start_time: str  # Format: HH:MM:SS,mmm
    end_time: str    # Format: HH:MM:SS,mmm
    text: str
    
    def to_srt(self) -> str:
        """Convert to SRT format string."""
        return f"{self.index}\n{self.start_time} --> {self.end_time}\n{self.text}\n"


def parse_srt(content: str) -> List[SubtitleEntry]:
    """
    Parse SRT content into a list of SubtitleEntry objects.
    
    Args:
        content: SRT file content.
        
    Returns:
        List of SubtitleEntry objects.
    """
    entries = []
    
    # Split by double newlines (subtitle blocks)
    blocks = re.split(r'\n\s*\n', content.strip())
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            try:
                index = int(lines[0])
                time_match = re.match(
                    r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})',
                    lines[1]
                )
                if time_match:
                    start_time = time_match.group(1)
                    end_time = time_match.group(2)
                    text = '\n'.join(lines[2:])
                    
                    entries.append(SubtitleEntry(
                        index=index,
                        start_time=start_time,
                        end_time=end_time,
                        text=text
                    ))
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse subtitle block: {e}")
                continue
    
    return entries


def entries_to_srt(entries: List[SubtitleEntry]) -> str:
    """
    Convert list of SubtitleEntry to SRT content.
    
    Args:
        entries: List of SubtitleEntry objects.
        
    Returns:
        SRT content string.
    """
    # Re-index entries
    lines = []
    for i, entry in enumerate(entries, 1):
        entry.index = i
        lines.append(entry.to_srt())
    
    return '\n'.join(lines)


def save_srt(
    content: str,
    media_path: str,
    language: str = 'en',
    suffix: str = ''
) -> str:
    """
    Save SRT content next to a media file.
    
    Uses SUBTITLE_LANGUAGE_NAMING_TYPE from config to format the language code
    in the filename. Also respects SHOW_SUBGEN_MARKER setting.
    
    Args:
        content: SRT content to save.
        media_path: Path to the media file.
        language: Language code for the subtitle (any format - will be converted).
        suffix: Optional suffix (e.g., '.hi'). Note: '.subgen' is added automatically
                if SHOW_SUBGEN_MARKER is true.
        
    Returns:
        Path to the saved SRT file.
    """
    from app.config import get_settings
    settings = get_settings()
    
    base = os.path.splitext(media_path)[0]
    
    # Format language according to SUBTITLE_LANGUAGE_NAMING_TYPE
    formatted_lang = format_language_for_filename(language)
    
    # Build filename parts
    parts = [base]
    
    # Add subgen marker if configured
    if settings.subtitle_naming.show_subgen_marker:
        parts.append('subgen')
    
    # Add language
    parts.append(formatted_lang)
    
    # Add optional suffix (e.g., 'hi' for hearing impaired)
    if suffix:
        suffix = suffix.lstrip('.')  # Remove leading dot if present
        parts.append(suffix)
    
    srt_path = '.'.join(parts) + '.srt'
    
    # Create directory if needed
    os.makedirs(os.path.dirname(srt_path) or '.', exist_ok=True)
    
    with open(srt_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    logger.info(f"Saved subtitle to: {srt_path}")
    return srt_path


def load_srt(path: str) -> str:
    """
    Load SRT content from a file.
    
    Args:
        path: Path to SRT file.
        
    Returns:
        SRT content string.
    """
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def find_existing_subtitles(media_path: str) -> List[Tuple[str, str]]:
    """
    Find existing subtitle files for a media file.
    
    Args:
        media_path: Path to the media file.
        
    Returns:
        List of tuples: (srt_path, language_code)
    """
    base = os.path.splitext(media_path)[0]
    media_dir = os.path.dirname(media_path)
    media_name = os.path.splitext(os.path.basename(media_path))[0]
    
    subtitles = []
    
    if not os.path.exists(media_dir):
        return subtitles
    
    for file in os.listdir(media_dir):
        if not file.endswith('.srt'):
            continue
        
        # Check if this subtitle belongs to our media file
        if not file.startswith(media_name):
            continue
        
        srt_path = os.path.join(media_dir, file)
        
        # Extract language code from filename
        # Format: filename.en.srt or filename.english.srt
        parts = file[:-4].split('.')  # Remove .srt and split
        
        if len(parts) >= 2:
            lang = parts[-1]  # Last part before .srt
            subtitles.append((srt_path, lang))
        else:
            subtitles.append((srt_path, 'unknown'))
    
    return subtitles


def subtitle_exists(media_path: str, language: str) -> bool:
    """
    Check if a subtitle already exists for a media file and language.
    
    Checks multiple naming formats (ISO 639-1, ISO 639-2, NAME, etc.) to catch
    subtitles created with different SUBTITLE_LANGUAGE_NAMING_TYPE settings.
    
    Args:
        media_path: Path to the media file.
        language: Language code (any format).
        
    Returns:
        True if subtitle exists.
    """
    base = os.path.splitext(media_path)[0]
    
    # Parse the language to get all possible format variations
    lang_code = LanguageCode.from_string(language)
    
    # Build list of possible language strings
    lang_variants = [language]  # Always check the raw input
    
    if lang_code != LanguageCode.NONE:
        # Add all format variations
        if lang_code.to_iso_639_1():
            lang_variants.append(lang_code.to_iso_639_1())
        if lang_code.to_iso_639_2_t():
            lang_variants.append(lang_code.to_iso_639_2_t())
        if lang_code.to_iso_639_2_b():
            lang_variants.append(lang_code.to_iso_639_2_b())
        if lang_code.to_name():
            lang_variants.append(lang_code.to_name())
        if lang_code.to_name(in_english=False):
            lang_variants.append(lang_code.to_name(in_english=False))
    
    # Remove duplicates while preserving order
    lang_variants = list(dict.fromkeys(lang_variants))
    
    # Check all patterns (with and without subgen marker)
    for lang in lang_variants:
        patterns = [
            f"{base}.{lang}.srt",
            f"{base}.subgen.{lang}.srt",
            f"{base}.{lang}.subgen.srt",  # Alternative order
        ]
        if any(os.path.exists(p) for p in patterns):
            return True
    
    return False


def get_srt_path(media_path: str, language: str, suffix: str = '') -> str:
    """
    Get the expected SRT path for a media file.
    
    Uses SUBTITLE_LANGUAGE_NAMING_TYPE from config to format the language code.
    Also respects SHOW_SUBGEN_MARKER setting.
    
    Args:
        media_path: Path to media file.
        language: Language code (any format - will be converted).
        suffix: Optional suffix (e.g., 'hi'). Note: '.subgen' is added automatically
                if SHOW_SUBGEN_MARKER is true.
        
    Returns:
        Expected SRT file path.
    """
    from app.config import get_settings
    settings = get_settings()
    
    base = os.path.splitext(media_path)[0]
    
    # Format language according to SUBTITLE_LANGUAGE_NAMING_TYPE
    formatted_lang = format_language_for_filename(language)
    
    # Build filename parts
    parts = [base]
    
    # Add subgen marker if configured
    if settings.subtitle_naming.show_subgen_marker:
        parts.append('subgen')
    
    # Add language
    parts.append(formatted_lang)
    
    # Add optional suffix
    if suffix:
        suffix = suffix.lstrip('.')  # Remove leading dot if present
        parts.append(suffix)
    
    return '.'.join(parts) + '.srt'


def srt_time_to_seconds(time_str: str) -> float:
    """
    Convert SRT time format to seconds.
    
    Args:
        time_str: Time in format HH:MM:SS,mmm
        
    Returns:
        Time in seconds.
    """
    match = re.match(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})', time_str)
    if not match:
        return 0.0
    
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def seconds_to_srt_time(seconds: float) -> str:
    """
    Convert seconds to SRT time format.
    
    Args:
        seconds: Time in seconds.
        
    Returns:
        Time in format HH:MM:SS,mmm
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def add_subgen_marker(content: str) -> str:
    """
    Add a marker comment to indicate the subtitle was generated by SubGen.
    
    Args:
        content: Original SRT content.
        
    Returns:
        SRT content with marker.
    """
    from datetime import datetime
    
    marker = f"\n\n# Generated by SubGen-Azure-Batch on {datetime.now().isoformat()}\n"
    return content + marker


def validate_srt(content: str) -> Tuple[bool, Optional[str]]:
    """
    Validate SRT content format.
    
    Args:
        content: SRT content to validate.
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not content.strip():
        return False, "Empty content"
    
    try:
        entries = parse_srt(content)
        if not entries:
            return False, "No valid subtitle entries found"
        
        # Check for time sequence issues
        prev_end = 0.0
        for entry in entries:
            start = srt_time_to_seconds(entry.start_time)
            end = srt_time_to_seconds(entry.end_time)
            
            if end <= start:
                return False, f"Entry {entry.index}: end time must be after start time"
        
        return True, None
        
    except Exception as e:
        return False, str(e)


# Import audio file utilities from audio_extractor (canonical location)
from app.audio_extractor import AUDIO_EXTENSIONS, is_audio_file


def write_lrc(
    entries: List[SubtitleEntry],
    output_path: str
) -> str:
    """
    Write subtitle entries to an LRC (lyrics) file.
    
    LRC format is commonly used for audio files and is simpler than SRT.
    Based on original subgen.py write_lrc() function.
    
    Args:
        entries: List of SubtitleEntry objects.
        output_path: Path to write the LRC file.
        
    Returns:
        Path to the written LRC file.
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        for entry in entries:
            # Convert SRT time to LRC time (mm:ss.xx)
            start_seconds = srt_time_to_seconds(entry.start_time)
            minutes = int(start_seconds // 60)
            seconds = int(start_seconds % 60)
            fraction = int((start_seconds - int(start_seconds)) * 100)
            
            # Remove embedded newlines, since some players ignore text after newlines
            text = entry.text.replace('\n', ' ').strip()
            
            f.write(f"[{minutes:02d}:{seconds:02d}.{fraction:02d}]{text}\n")
    
    logger.info(f"Saved LRC lyrics to: {output_path}")
    return output_path


def get_lrc_path(media_path: str, language: str) -> str:
    """
    Get the expected LRC path for a media file.
    
    Args:
        media_path: Path to media file.
        language: Language code.
        
    Returns:
        Expected LRC file path.
    """
    from app.config import get_settings
    settings = get_settings()
    
    base = os.path.splitext(media_path)[0]
    
    # Format language according to SUBTITLE_LANGUAGE_NAMING_TYPE
    formatted_lang = format_language_for_filename(language)
    
    # Build filename parts
    parts = [base]
    
    # Add subgen marker if configured
    if settings.subtitle_naming.show_subgen_marker:
        parts.append('subgen')
    
    # Add language
    parts.append(formatted_lang)
    
    return '.'.join(parts) + '.lrc'


def save_lrc(
    content: str,
    media_path: str,
    language: str = 'en'
) -> str:
    """
    Save subtitle content as an LRC file next to a media file.
    
    First parses the SRT content to get entries, then writes as LRC.
    
    Args:
        content: SRT content to convert and save.
        media_path: Path to the media file.
        language: Language code for the subtitle.
        
    Returns:
        Path to the saved LRC file.
    """
    # Parse the SRT content to get entries
    entries = parse_srt(content)
    
    # Get the LRC path
    lrc_path = get_lrc_path(media_path, language)
    
    # Create directory if needed
    os.makedirs(os.path.dirname(lrc_path) or '.', exist_ok=True)
    
    # Write LRC file
    return write_lrc(entries, lrc_path)


def append_credit_line(content: str, time_offset: float = 5.0) -> str:
    """
    Append a credit line at the end of SRT content.
    
    Based on original subgen.py appendLine() function. Adds a segment
    indicating the file was transcribed by SubGen-Azure-Batch.
    
    Args:
        content: Original SRT content.
        time_offset: Seconds after last segment to show credit.
        
    Returns:
        SRT content with appended credit line.
    """
    from datetime import datetime
    
    entries = parse_srt(content)
    if not entries:
        return content
    
    # Get the last segment's timing
    last_entry = entries[-1]
    last_end = srt_time_to_seconds(last_entry.end_time)
    
    # Create credit segment timing
    credit_start = last_end + time_offset
    credit_end = credit_start + time_offset
    
    date_time_str = datetime.now().strftime("%d %b %Y - %H:%M:%S")
    credit_text = f"Transcribed by SubGen-Azure-Batch on {date_time_str}"
    
    # Format as SRT entry
    credit_entry = SubtitleEntry(
        index=last_entry.index + 1,
        start_time=seconds_to_srt_time(credit_start),
        end_time=seconds_to_srt_time(credit_end),
        text=credit_text
    )
    
    # Append to content
    return content.rstrip() + '\n\n' + credit_entry.to_srt()
