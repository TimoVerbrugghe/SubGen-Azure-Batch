"""
Webhook Router - Media server webhook handlers.

Provides endpoints for receiving webhooks from:
- Plex
- Jellyfin
- Emby
- Tautulli

When a new media file is added, these webhooks trigger subtitle generation.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request

from app.config import get_settings
from app.utils.audio_extractor import extract_audio
from app.utils.azure_batch_transcriber import AzureBatchTranscriber
from app.utils.bazarr_client import BazarrClient, notify_bazarr_of_new_subtitle
from app.utils.language_code import LanguageCode
from app.utils.media_server_client import (JellyfinClient, PlexClient,
                                           refresh_all_configured_servers)
from app.utils.skip_checker import should_skip_file
from app.utils.subtitle_utils import (append_credit_line,
                                      format_language_for_filename,
                                      get_srt_path, is_audio_file, save_lrc,
                                      save_srt)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhooks"])

# Track active transcription jobs to prevent duplicates
_active_jobs: Dict[str, bool] = {}


async def process_media_file(
    file_path: str,
    language: str = "en",
    media_type: Optional[str] = None,
    series_id: Optional[int] = None,
    movie_id: Optional[int] = None,
    plex_item_id: Optional[str] = None,
    jellyfin_item_id: Optional[str] = None,
    emby_item_id: Optional[str] = None,
):
    """
    Process a media file for subtitle generation.
    
    Args:
        file_path: Path to the media file.
        language: Language code for transcription.
        media_type: Type of media ('episode' or 'movie').
        series_id: Bazarr series ID (for TV shows).
        movie_id: Bazarr movie ID (for movies).
        plex_item_id: Plex rating key for metadata refresh.
        jellyfin_item_id: Jellyfin item ID for metadata refresh.
        emby_item_id: Emby item ID for metadata refresh.
    """
    settings = get_settings()
    path = Path(file_path)
    
    if not path.exists():
        logger.error(f"Media file not found: {file_path}")
        return
    
    # Apply FORCE_DETECTED_LANGUAGE_TO if configured
    if settings.transcription.force_language:
        original_language = language
        language = settings.transcription.force_language
        logger.info(f"Forcing language from '{original_language}' to '{language}' (FORCE_DETECTED_LANGUAGE_TO)")
    
    # Check skip conditions (existing subtitles, internal subs, audio language, etc.)
    skip_result = await should_skip_file(file_path, language)
    if skip_result.should_skip:
        logger.info(f"Skipping {path.name}: {skip_result.reason}")
        return
    
    logger.info(f"Processing media file: {file_path}")
    
    # Check if this is an audio file (for LRC support)
    is_audio = is_audio_file(file_path)
    
    # Determine output path (LRC for audio files if configured, SRT otherwise)
    if is_audio and settings.transcription.lrc_for_audio_files:
        logger.info("Audio file detected with LRC_FOR_AUDIO_FILES enabled - will save as LRC")
    
    # Determine output SRT path
    srt_path = get_srt_path(file_path, language)
    
    try:
        # Convert language to Azure locale
        try:
            lang_code = LanguageCode(language.lower())
            azure_locale = lang_code.to_azure_locale()
        except ValueError:
            azure_locale = f"{language}-US" if len(language) == 2 else language
        
        # Determine which audio track to extract (PREFERRED_AUDIO_LANGUAGES)
        audio_track = 0  # Default to first track
        if not is_audio and settings.transcription.preferred_audio_languages_list:
            from app.utils.audio_extractor import (find_preferred_audio_track,
                                                   get_audio_tracks)
            audio_tracks = await get_audio_tracks(file_path)
            if audio_tracks:
                preferred_langs = settings.transcription.preferred_audio_languages_list
                audio_track, detected_lang = find_preferred_audio_track(audio_tracks, preferred_langs)
                if detected_lang:
                    logger.info(f"Selected audio track {audio_track} with language '{detected_lang}'")
        
        # Extract audio from video
        audio_path = await extract_audio(file_path, audio_track=audio_track)
        logger.info(f"Extracted audio to: {audio_path}")
        
        # Create transcriber
        transcriber = AzureBatchTranscriber()
        
        try:
            # Upload to Azure
            audio_url, blob_name = await transcriber.upload_audio(audio_path)
            logger.info(f"Uploaded audio to Azure: {blob_name}")
            
            # Create transcription job
            job = await transcriber.create_transcription(
                audio_url=audio_url,
                locale=azure_locale,
                display_name=f"webhook-{path.stem}"
            )
            logger.info(f"Created transcription job: {job.id}")
            
            # Wait for transcription
            result = await transcriber.wait_for_transcription(job.id)
            logger.info(f"Transcription completed: {len(result.segments)} segments")
            
            # Generate SRT content
            srt_content = result.to_srt()
            
            # Append credit line if configured (APPEND)
            if settings.transcription.append_credit_line:
                srt_content = append_credit_line(srt_content)
                logger.debug("Appended credit line to subtitle")
            
            # Save subtitle file
            if is_audio and settings.transcription.lrc_for_audio_files:
                # Save as LRC for audio files
                lrc_path = save_lrc(srt_content, file_path, language)
                logger.info(f"Saved LRC lyrics: {lrc_path}")
            else:
                # Save as SRT for video files
                save_srt(srt_content, srt_path)
                logger.info(f"Saved subtitle: {srt_path}")
            
            # Notify Bazarr if configured
            if settings.bazarr.is_configured:
                try:
                    if media_type == "episode" and series_id:
                        # We have the Sonarr series ID, use it directly
                        bazarr = BazarrClient(settings.bazarr.url, settings.bazarr.api_key)
                        try:
                            await bazarr.trigger_series_scan(series_id)
                            logger.info(f"Notified Bazarr: series scan for ID {series_id}")
                        finally:
                            await bazarr.close()
                    elif media_type == "movie" and movie_id:
                        # We have the Radarr movie ID, use it directly
                        bazarr = BazarrClient(settings.bazarr.url, settings.bazarr.api_key)
                        try:
                            await bazarr.trigger_movie_scan(movie_id)
                            logger.info(f"Notified Bazarr: movie scan for ID {movie_id}")
                        finally:
                            await bazarr.close()
                    else:
                        # No ID available (e.g., from Plex/Jellyfin webhook)
                        # Use smart path-based lookup to find the series/movie
                        if await notify_bazarr_of_new_subtitle(file_path):
                            logger.info("Notified Bazarr of new subtitle (path-based lookup)")
                        else:
                            logger.debug("Bazarr notification skipped or failed")
                except Exception as e:
                    logger.warning(f"Failed to notify Bazarr: {e}")
            
            # Refresh media server metadata so they pick up the new subtitle
            if plex_item_id or jellyfin_item_id or emby_item_id:
                try:
                    refresh_results = await refresh_all_configured_servers(
                        plex_item_id=plex_item_id,
                        jellyfin_item_id=jellyfin_item_id,
                        emby_item_id=emby_item_id,
                    )
                    refreshed = [k for k, v in refresh_results.items() if v]
                    if refreshed:
                        logger.info(f"Refreshed metadata on: {', '.join(refreshed)}")
                except Exception as e:
                    logger.warning(f"Media server refresh failed: {e}")
            
            # Cleanup transcription job and blob
            await transcriber.delete_transcription(job.id)
            try:
                await transcriber.delete_blob(blob_name)
            except Exception as e:
                logger.warning(f"Failed to delete blob {blob_name}: {e}")
            
        finally:
            await transcriber.close()
            
            # Cleanup audio file
            try:
                Path(audio_path).unlink()
            except Exception:
                pass
                
    except Exception as e:
        logger.exception(f"Failed to process {file_path}: {e}")
    finally:
        # Remove from active jobs
        if file_path in _active_jobs:
            del _active_jobs[file_path]


def start_transcription_task(
    background_tasks: BackgroundTasks,
    file_path: str,
    language: str = "en",
    media_type: Optional[str] = None,
    series_id: Optional[int] = None,
    movie_id: Optional[int] = None,
    plex_item_id: Optional[str] = None,
    jellyfin_item_id: Optional[str] = None,
    emby_item_id: Optional[str] = None,
):
    """Start a background transcription task, avoiding duplicates."""
    if file_path in _active_jobs:
        logger.info(f"Transcription already in progress for: {file_path}")
        return False
    
    # Mark as active
    _active_jobs[file_path] = True
    
    background_tasks.add_task(
        process_media_file,
        file_path=file_path,
        language=language,
        media_type=media_type,
        series_id=series_id,
        movie_id=movie_id,
        plex_item_id=plex_item_id,
        jellyfin_item_id=jellyfin_item_id,
        emby_item_id=emby_item_id,
    )
    return True


@router.post("/plex")
async def plex_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle Plex webhooks.
    
    Plex sends webhooks for various events. We're interested in:
    - library.new: New media added
    - media.play: Media started playing (optional trigger)
    
    After starting transcription, we immediately trigger a metadata refresh
    so Plex will pick up the subtitle once it's created.
    """
    settings = get_settings()
    
    try:
        # Plex sends multipart form data
        form = await request.form()
        payload_str = form.get("payload", "{}")
        # Ensure we have a string (form.get can return UploadFile for file fields)
        if not isinstance(payload_str, str):
            payload_str = "{}"
        payload = json.loads(payload_str)
        
        event = payload.get("event", "")
        logger.info(f"Plex webhook event: {event}")
        
        # Check processing control settings
        should_process = False
        if event == "library.new" and settings.processing.process_added_media:
            should_process = True
        elif event == "media.play" and settings.processing.process_on_play:
            should_process = True
        
        if not should_process:
            return {"status": "ignored", "event": event}
        
        metadata = payload.get("Metadata", {})
        media_type = metadata.get("type", "")
        rating_key = metadata.get("ratingKey", "")  # Plex item ID
        
        # Get file path from Plex metadata
        media_list = metadata.get("Media", [])
        if not media_list:
            return {"status": "no_media"}
        
        transcription_started = False
        for media in media_list:
            for part in media.get("Part", []):
                file_path = part.get("file", "")
                if file_path:
                    # Apply path mapping
                    file_path = settings.path_mapping.apply(file_path)
                    
                    if Path(file_path).exists():
                        started = start_transcription_task(
                            background_tasks,
                            file_path=file_path,
                            language=settings.subtitle_language,
                            media_type="episode" if media_type == "episode" else "movie",
                            plex_item_id=rating_key if rating_key else None,
                        )
                        if started:
                            logger.info(f"Started transcription for: {file_path}")
                            transcription_started = True
        
        # Note: Metadata refresh happens AFTER transcription completes in process_media_file
        
        return {"status": "processing" if transcription_started else "no_files"}
        
    except Exception as e:
        logger.exception(f"Plex webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/jellyfin")
async def jellyfin_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle Jellyfin webhooks.
    
    Jellyfin webhook plugin sends JSON payloads for various events.
    After starting transcription, we trigger a metadata refresh so
    Jellyfin picks up the subtitle once it's created.
    """
    settings = get_settings()
    
    try:
        payload = await request.json()
        
        event_type = payload.get("NotificationType", payload.get("EventType", ""))
        logger.info(f"Jellyfin webhook event: {event_type}")
        
        # Check processing control settings
        should_process = False
        if event_type == "ItemAdded" and settings.processing.process_added_media:
            should_process = True
        elif event_type == "PlaybackStart" and settings.processing.process_on_play:
            should_process = True
        
        if not should_process:
            return {"status": "ignored", "event": event_type}
        
        # Get item ID for metadata refresh
        item_id = payload.get("ItemId", payload.get("Item", {}).get("Id", ""))
        
        # Get file path
        file_path = payload.get("Path", payload.get("Item", {}).get("Path", ""))
        if not file_path:
            return {"status": "no_path"}
        
        # Apply path mapping
        file_path = settings.path_mapping.apply(file_path)
        
        if not Path(file_path).exists():
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        
        item_type = payload.get("ItemType", payload.get("Item", {}).get("Type", ""))
        
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type="episode" if item_type in ("Episode",) else "movie",
            jellyfin_item_id=item_id if item_id else None,
        )
        
        # Note: Metadata refresh happens AFTER transcription completes in process_media_file
        
        return {"status": "processing" if started else "already_processing"}
        
    except Exception as e:
        logger.exception(f"Jellyfin webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emby")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle Emby webhooks.
    
    Emby webhooks are similar to Jellyfin.
    After starting transcription, we trigger a metadata refresh so
    Emby picks up the subtitle once it's created.
    """
    settings = get_settings()
    
    try:
        payload = await request.json()
        
        event_type = payload.get("Event", "")
        logger.info(f"Emby webhook event: {event_type}")
        
        # Check processing control settings
        should_process = False
        if event_type == "library.new" and settings.processing.process_added_media:
            should_process = True
        elif event_type == "playback.start" and settings.processing.process_on_play:
            should_process = True
        
        if not should_process:
            return {"status": "ignored", "event": event_type}
        
        # Get item details
        item = payload.get("Item", {})
        item_id = item.get("Id", "")
        file_path = item.get("Path", "")
        
        if not file_path:
            return {"status": "no_path"}
        
        # Apply path mapping
        file_path = settings.path_mapping.apply(file_path)
        
        if not Path(file_path).exists():
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        
        item_type = item.get("Type", "")
        
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type="episode" if item_type == "Episode" else "movie",
            emby_item_id=item_id if item_id else None,
        )
        
        # Note: Metadata refresh happens AFTER transcription completes in process_media_file
        
        return {"status": "processing" if started else "already_processing"}
        
    except Exception as e:
        logger.exception(f"Emby webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tautulli")
async def tautulli_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    file: str = Form(None),
    media_type: str = Form(None),
):
    """
    Handle Tautulli webhooks.
    
    Tautulli can send custom webhooks with file path information.
    Configure Tautulli to send: file={file}, media_type={media_type}
    """
    settings = get_settings()
    
    try:
        # Try form data first
        if file:
            file_path = file
        else:
            # Try JSON body
            try:
                payload = await request.json()
                file_path = payload.get("file", "")
                media_type = payload.get("media_type", media_type)
            except Exception:
                file_path = ""
        
        if not file_path:
            return {"status": "no_file"}
        
        logger.info(f"Tautulli webhook for: {file_path}")
        
        if not Path(file_path).exists():
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type=media_type or "movie",
        )
        
        return {"status": "processing" if started else "already_processing"}
        
    except Exception as e:
        logger.exception(f"Tautulli webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def webhook_status():
    """Get status of active webhook-triggered transcription jobs."""
    return {
        "active_jobs": len(_active_jobs),
        "job_paths": list(_active_jobs.keys())[:10],  # Limit to first 10
    }
