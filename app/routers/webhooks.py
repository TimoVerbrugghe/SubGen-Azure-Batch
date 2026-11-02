"""Media-server webhooks that enqueue unified, tracked subtitle generation."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request

from app.config import PathMappingConfig, get_settings, require_azure_configured
from app.transcription_service import JobSource, TranscriptionService
from app.utils.audio_extractor import (
    find_preferred_audio_track,
    get_audio_tracks,
    is_audio_file,
)
from app.utils.bazarr_client import (
    BazarrClient,
    BazarrScanOutcome,
    notify_bazarr_of_new_subtitle,
)
from app.utils.media_server_client import MediaRefreshContext
from app.utils.skip_checker import should_skip_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["Webhooks"])
_active_jobs: dict[str, bool] = {}


def _local_media_path(server: str, path: str) -> str:
    settings = get_settings()
    mapping = getattr(settings, server).path_mapping
    if mapping.enabled:
        return PathMappingConfig(
            enabled=True, from_path=mapping.to_path, to_path=mapping.from_path
        ).apply(path)
    return settings.path_mapping.apply(path)


async def process_media_file(
    file_path: str,
    language: str = "en",
    media_type: str | None = None,
    series_id: int | None = None,
    movie_id: int | None = None,
    plex_item_id: str | None = None,
    jellyfin_item_id: str | None = None,
    emby_item_id: str | None = None,
    server_paths: dict[str, str] | None = None,
) -> None:
    """Generate subtitles through the shared service, then notify Bazarr."""
    settings = get_settings()
    try:
        if not await asyncio.to_thread(Path(file_path).exists):
            logger.error(f"Media file not found: {file_path}")
            return
        language = settings.transcription.force_language or language
        skip_result = await should_skip_file(file_path, language)
        if skip_result.should_skip:
            logger.warning(f"Skipping {Path(file_path).name}: {skip_result.reason}")
            return

        audio_track = 0
        if (
            not is_audio_file(file_path)
            and settings.transcription.preferred_audio_languages_list
        ):
            tracks = await get_audio_tracks(file_path)
            if tracks:
                audio_track, _ = find_preferred_audio_track(
                    tracks, settings.transcription.preferred_audio_languages_list
                )
        context = MediaRefreshContext(
            item_ids={
                name: str(item_id)
                for name, item_id in (
                    ("plex", plex_item_id),
                    ("jellyfin", jellyfin_item_id),
                    ("emby", emby_item_id),
                )
                if item_id
            },
            server_paths=server_paths or {},
        )
        result, job = await TranscriptionService.transcribe_file(
            file_path=file_path,
            language=language,
            source=JobSource.WEBHOOK,
            media_refresh_context=context,
            audio_track=audio_track,
        )
        if result is None or not job.srt_path:
            return

        if settings.bazarr.is_configured:
            if (media_type == "episode" and series_id) or (
                media_type == "movie" and movie_id
            ):
                bazarr = BazarrClient(settings.bazarr.url, settings.bazarr.api_key)
                try:
                    outcome = (
                        await bazarr.trigger_series_scan_outcome(series_id)
                        if media_type == "episode" and series_id
                        else await bazarr.trigger_movie_scan_outcome(movie_id)
                    )
                    if outcome is BazarrScanOutcome.CONFIRMED:
                        logger.info("Bazarr: Targeted subtitle scan confirmed")
                    else:
                        logger.warning(
                            f"Bazarr: Targeted scan not confirmed ({outcome.value})"
                        )
                finally:
                    await bazarr.close()
            elif not await notify_bazarr_of_new_subtitle(file_path):
                logger.warning("Bazarr: Subtitle scan not confirmed")
    except Exception:
        logger.exception(f"Failed to process webhook media: {file_path}")
    finally:
        _active_jobs.pop(file_path, None)


def start_transcription_task(
    background_tasks: BackgroundTasks,
    file_path: str,
    language: str = "en",
    media_type: str | None = None,
    series_id: int | None = None,
    movie_id: int | None = None,
    plex_item_id: str | None = None,
    jellyfin_item_id: str | None = None,
    emby_item_id: str | None = None,
    server_paths: dict[str, str] | None = None,
) -> bool:
    """Enqueue a webhook job once; release duplicate tracking even when skipped."""
    if file_path in _active_jobs:
        logger.info(f"Transcription already in progress for: {file_path}")
        return False
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
        server_paths=server_paths,
    )
    return True


@router.post("/plex")
async def plex_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Accept Plex added/play events and retain the item rating key for refresh."""
    settings = get_settings()
    try:
        form = await request.form()
        payload_str = form.get("payload", "{}")
        payload = json.loads(payload_str if isinstance(payload_str, str) else "{}")
        event = payload.get("event", "")
        logger.info(f"Plex webhook event: {event}")
        if not (
            event == "library.new"
            and settings.processing.process_added_media
            or event == "media.play"
            and settings.processing.process_on_play
        ):
            return {"status": "ignored", "event": event}
        require_azure_configured()
        metadata = payload.get("Metadata", {})
        media_type = metadata.get("type", "")
        if media_type and media_type not in ("movie", "episode"):
            return {"status": "ignored", "event": event}
        media_list = metadata.get("Media", [])
        if not media_list:
            return {"status": "no_media"}
        started = False
        for media in media_list:
            for part in media.get("Part", []):
                server_path = part.get("file", "")
                if not server_path:
                    continue
                file_path = _local_media_path("plex", server_path)
                if await asyncio.to_thread(Path(file_path).exists):
                    started = (
                        start_transcription_task(
                            background_tasks,
                            file_path=file_path,
                            language=settings.subtitle_language,
                            media_type=media_type or "movie",
                            plex_item_id=str(metadata["ratingKey"])
                            if metadata.get("ratingKey")
                            else None,
                            server_paths={"plex": server_path},
                        )
                        or started
                    )
                else:
                    logger.warning(f"Plex: File not found: {file_path}")
        return {"status": "processing" if started else "no_files"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Plex webhook error")
        raise HTTPException(
            status_code=500, detail="Plex webhook processing failed"
        ) from None


@router.post("/jellyfin")
async def jellyfin_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Accept Jellyfin plugin events, preserving episode/movie IDs and paths."""
    settings = get_settings()
    try:
        payload = await request.json()
        event = payload.get("NotificationType", payload.get("EventType", ""))
        logger.info(f"Jellyfin webhook event: {event}")
        if not (
            event == "ItemAdded"
            and settings.processing.process_added_media
            or event == "PlaybackStart"
            and settings.processing.process_on_play
        ):
            return {"status": "ignored", "event": event}
        require_azure_configured()
        item = payload.get("Item", {})
        item_type = payload.get("ItemType", item.get("Type", ""))
        if item_type and item_type not in ("Episode", "Movie"):
            return {"status": "ignored", "event": event}
        item_id = payload.get("ItemId", item.get("Id", ""))
        server_path = payload.get("Path", item.get("Path", ""))
        if not server_path:
            return {"status": "no_path"}
        file_path = _local_media_path("jellyfin", server_path)
        if not await asyncio.to_thread(Path(file_path).exists):
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type="episode" if item_type == "Episode" else "movie",
            jellyfin_item_id=item_id or None,
            server_paths={"jellyfin": server_path},
        )
        return {"status": "processing" if started else "already_processing"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Jellyfin webhook error")
        raise HTTPException(
            status_code=500, detail="Jellyfin webhook processing failed"
        ) from None


@router.post("/emby")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Accept Emby added/play events through the same transcription pipeline."""
    settings = get_settings()
    try:
        payload = await request.json()
        event = payload.get("Event", "")
        logger.info(f"Emby webhook event: {event}")
        if not (
            event == "library.new"
            and settings.processing.process_added_media
            or event == "playback.start"
            and settings.processing.process_on_play
        ):
            return {"status": "ignored", "event": event}
        require_azure_configured()
        item = payload.get("Item", {})
        item_type = item.get("Type", "")
        if item_type and item_type not in ("Episode", "Movie"):
            return {"status": "ignored", "event": event}
        server_path = item.get("Path", "")
        if not server_path:
            return {"status": "no_path"}
        file_path = _local_media_path("emby", server_path)
        if not await asyncio.to_thread(Path(file_path).exists):
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type="episode" if item_type == "Episode" else "movie",
            emby_item_id=item.get("Id") or None,
            server_paths={"emby": server_path},
        )
        return {"status": "processing" if started else "already_processing"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Emby webhook error")
        raise HTTPException(
            status_code=500, detail="Emby webhook processing failed"
        ) from None


@router.post("/tautulli")
async def tautulli_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    file: str | None = Form(None),
    media_type: str | None = Form(None),
) -> dict:
    """Accept Tautulli's file/media_type payload and refresh by path after saving."""
    settings = get_settings()
    try:
        if file:
            server_path = file
        else:
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("Tautulli: Missing or invalid JSON payload")
                return {"status": "no_file"}
            server_path = payload.get("file", "")
            media_type = payload.get("media_type", media_type)
        if not server_path:
            return {"status": "no_file"}
        require_azure_configured()
        file_path = _local_media_path("plex", server_path)
        if not await asyncio.to_thread(Path(file_path).exists):
            logger.warning(f"File not found: {file_path}")
            return {"status": "file_not_found"}
        started = start_transcription_task(
            background_tasks,
            file_path=file_path,
            language=settings.subtitle_language,
            media_type=media_type or "movie",
            server_paths={"plex": server_path},
        )
        return {"status": "processing" if started else "already_processing"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Tautulli webhook error")
        raise HTTPException(
            status_code=500, detail="Tautulli webhook processing failed"
        ) from None


@router.get("/status")
async def webhook_status() -> dict:
    """Return active webhook jobs."""
    return {"active_jobs": len(_active_jobs), "job_paths": list(_active_jobs)}
