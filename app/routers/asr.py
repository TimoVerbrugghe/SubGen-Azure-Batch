"""
ASR Router - Bazarr-compatible transcription endpoint.

Provides the /asr and /detect-language endpoints that mimic the Whisper ASR API format,
allowing Bazarr to use SubGen-Azure-Batch as a transcription provider.
"""

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Annotated, Literal

import aiofiles
import aiohttp
import anyio
from azure.core.exceptions import AzureError
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import (
    SUBGEN_AZURE_BATCH_VERSION,
    get_settings,
    require_azure_configured,
)
from app.transcription_service import JobSource, TranscriptionService
from app.utils.audio_extractor import extract_audio_segment, make_temp_dir
from app.utils.language_code import LanguageCode

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ASR"])


async def _keepalive_stream(
    operation: Callable[[], Coroutine[object, object, bytes]],
    interval: float,
) -> AsyncIterator[bytes]:
    processing = asyncio.create_task(operation())
    try:
        # Blank lines are valid before SRT cues and JSON documents.
        yield b"\n"
        while not processing.done():
            done, _ = await asyncio.wait((processing,), timeout=interval)
            if not done:
                yield b"\n"
        yield await processing
    finally:
        processing.cancel()
        with anyio.CancelScope(shield=True):
            try:
                await processing
            except asyncio.CancelledError:
                pass


async def _asr_response(
    operation: Callable[[], Coroutine[object, object, bytes]],
    media_type: str,
    keepalive_interval: float,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Response:
    async def run() -> bytes:
        try:
            return await asyncio.wait_for(operation(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError) as error:
            logger.exception(f"Bazarr request exceeded processing timeout ({timeout}s)")
            raise HTTPException(
                status_code=504, detail="ASR processing timed out"
            ) from error
        except (
            RuntimeError,
            ValueError,
            OSError,
            aiohttp.ClientError,
            AzureError,
        ) as error:
            logger.exception("Bazarr request processing failed")
            raise HTTPException(
                status_code=500, detail="ASR processing failed; see server logs"
            ) from error

    if keepalive_interval > 0:
        return StreamingResponse(
            _keepalive_stream(run, keepalive_interval),
            media_type=media_type,
            headers={**(headers or {}), "X-Accel-Buffering": "no"},
        )
    return Response(content=await run(), media_type=media_type, headers=headers)


@router.get("/")
async def get_root_version() -> str:
    """
    Return version info for Bazarr compatibility at root.
    Mimics whisper-asr-webservice format.
    """
    return f"Whisper ASR Webservice {SUBGEN_AZURE_BATCH_VERSION} (SubGen-Azure-Batch)"


@router.get("/asr")
async def get_asr_version() -> list[str]:
    """
    Return error message for GET requests (matches original subgen behavior).

    The actual transcription happens via POST /asr.
    """
    return [
        "You accessed this request incorrectly via a GET request.  See https://github.com/TimoVerbrugghe/subgen-azure-batch for proper configuration"
    ]


@router.get("/status")
async def get_status() -> dict[str, str]:
    """
    Return version info for Bazarr compatibility.

    Bazarr checks this endpoint to verify the ASR provider is working and get version info.
    Matches the original subgen format: {"version": "Subgen X.Y.Z, ..."}
    """
    return {
        "version": f"SubGen-Azure-Batch {SUBGEN_AZURE_BATCH_VERSION}, Azure Batch Transcription API"
    }


@router.post("/asr")
async def transcribe_audio(
    audio_file: Annotated[UploadFile, File()],
    task: Literal["transcribe", "translate"] | None = Query(default="transcribe"),
    language: str | None = Query(default=None),
    video_file: str | None = Query(default=None),  # For logging (from Bazarr)
    initial_prompt: str | None = Query(
        default=None
    ),  # Not used, kept for compatibility
    encode: bool = Query(default=True, description="Encode audio first through ffmpeg"),
    output: Literal["txt", "vtt", "srt", "tsv", "json"] | None = Query(default="srt"),
    word_timestamps: bool = Query(default=False, description="Word-level timestamps"),
) -> Response:
    """
    Transcribe audio file using Azure Batch Transcription.

    This endpoint is compatible with Whisper ASR API format used by Bazarr.

    Args:
        audio_file: Audio file to transcribe (WAV, MP3, etc.)
        task: Task type ('transcribe' or 'translate'). Only 'transcribe' is supported.
        language: Language code (e.g., 'en', 'es', 'de'). If None, auto-detect.
        video_file: Original video file path (for logging purposes).
        initial_prompt: Initial prompt (not used, kept for API compatibility).
        encode: Whether the uploaded audio is encoded rather than raw PCM.
        output: Output format ('srt', 'vtt', 'txt'). Default is 'srt'.
        word_timestamps: Whether to include word-level timestamps. Default False.

    Returns:
        Transcription in requested format (SRT by default).

    Raises:
        HTTPException: If transcription fails or configuration is invalid.
    """
    require_azure_configured()
    settings = get_settings()

    # Log the request with video file info if available
    if video_file:
        logger.info(f"Transcribe request for '{video_file}' from Bazarr/ASR webhook")
    else:
        logger.info("Transcribe request from Bazarr/ASR webhook")

    # Only transcription is supported (no translation via Azure Batch API)
    if task == "translate":
        logger.warning(
            "Translation task requested but not supported by Azure Batch API, falling back to transcribe"
        )

    # Determine output format
    output_format = (output or "srt").lower()

    try:
        content = await audio_file.read()
    finally:
        await audio_file.close()
    if not content:
        logger.error("Empty Bazarr audio upload")
        raise HTTPException(status_code=400, detail="Audio upload is empty")
    logger.debug(f"Received audio data: {len(content)} bytes, encode={encode}")

    async def transcribe() -> bytes:
        result, _ = await TranscriptionService.transcribe_audio_data(
            audio_data=content,
            language=language or "en",
            source=JobSource.BAZARR,
            file_name=video_file or audio_file.filename or "unknown",
            is_raw_pcm=not encode,  # If encode=False, it's raw PCM
        )

        if output_format == "srt":
            output_content = result.to_srt()
        elif output_format == "vtt":
            output_content = _srt_to_vtt(result.to_srt())
        elif output_format == "txt":
            output_content = result.text
        else:
            output_content = result.to_srt()

        return output_content.encode("utf-8")

    return await _asr_response(
        transcribe,
        media_type="text/plain",
        keepalive_interval=settings.asr_keepalive_interval
        if output_format == "srt"
        else 0,
        timeout=settings.transcription_timeout,
        headers={
            "Source": "Transcribed using Azure Batch API from SubGen-Azure-Batch!"
        },
    )


@router.get("/asr/languages")
async def list_languages() -> dict[str, list[dict[str, str | tuple[str | None, ...]]]]:
    """
    List supported languages for transcription.

    Returns:
        List of supported language codes with their names.
    """
    languages = []
    for lang in LanguageCode:
        languages.append(
            {
                "code": lang.value,
                "name": lang.name.replace("_", " ").title(),
                "azure_locale": lang.to_azure_locale(),
            }
        )
    return {"languages": languages}


@router.get("/detect-language")
async def get_detect_language_version() -> list[str]:
    """
    Return error message for GET requests (matches original subgen behavior).

    The actual language detection happens via POST /detect-language.
    """
    return [
        "You accessed this request incorrectly via a GET request.  See https://github.com/TimoVerbrugghe/subgen-azure-batch for proper configuration"
    ]


@router.post("//detect-language", response_model=None)
@router.post("/detect-language", response_model=None)
async def detect_language(
    audio_file: Annotated[UploadFile, File()],
    encode: bool = Query(default=True, description="Encode audio first through ffmpeg"),
    video_file: str | None = Query(default=None),
    detect_lang_length: int | None = Query(
        default=None, ge=1, description="Detect language on X seconds of the file"
    ),
    detect_lang_offset: int | None = Query(
        default=None, ge=0, description="Start detect language X seconds into the file"
    ),
) -> Response | dict[str, str]:
    """
    Detect the language of an audio file using Azure Batch Transcription.

    This endpoint is called by Bazarr before transcription to determine
    the language of the audio track.

    Args:
        audio_file: Audio file to analyze.
        encode: Whether the audio needs encoding (from Bazarr this is usually False).
        video_file: Original video file path (for logging purposes).
        detect_lang_length: How many seconds of audio to analyze.
        detect_lang_offset: Start offset in seconds.

    Returns:
        Dictionary with detected_language (name) and language_code (ISO 639-1).
    """
    settings = get_settings()

    # Apply config defaults if not specified in request
    if detect_lang_length is None:
        detect_lang_length = settings.transcription.detect_language_length
    if detect_lang_offset is None:
        detect_lang_offset = settings.transcription.detect_language_offset

    # Log the request with video file info if available
    if video_file:
        logger.info(
            f"Detecting language for file '{video_file}' from Bazarr/detect-language webhook"
        )
    else:
        logger.info("Detecting language from Bazarr/detect-language webhook")

    force_language = settings.transcription.forced_language
    if force_language:
        lang_code = LanguageCode.from_string(force_language)
        if lang_code != LanguageCode.NONE:
            logger.debug(f"Skipping detect language, forced to {lang_code.to_name()}")
            await audio_file.close()
            return {
                "detected_language": lang_code.to_name(),
                "language_code": lang_code.to_iso_639_1(),
            }
        logger.error("Invalid FORCE_DETECTED_LANGUAGE_TO configuration")
        raise HTTPException(status_code=503, detail="Forced language is not supported")

    require_azure_configured()
    candidate_locales = [
        locale.strip()
        for locale in settings.transcription.language_detection_candidates.split(",")
        if locale.strip()
    ]
    if not 1 <= len(candidate_locales) <= 4:
        logger.error("LANGUAGE_DETECTION_CANDIDATES must contain one to four locales")
        raise HTTPException(
            status_code=503,
            detail="Language detection requires one to four candidate locales",
        )

    try:
        content = await audio_file.read()
    finally:
        await audio_file.close()
    if not content:
        logger.error("Empty Bazarr language-detection upload")
        raise HTTPException(status_code=400, detail="Audio upload is empty")

    async def detect() -> bytes:
        temp_dir: str | None = None
        segment_audio: str | None = None
        try:
            if encode:
                temp_dir = await asyncio.to_thread(
                    make_temp_dir, prefix="subgen_detect_"
                )
                temp_input = Path(temp_dir) / "audio"
                async with aiofiles.open(temp_input, "wb") as audio:
                    await audio.write(content)
                segment_audio = await extract_audio_segment(
                    str(temp_input),
                    offset=float(detect_lang_offset),
                    duration=float(detect_lang_length),
                    output_format="wav",
                    sample_rate=16000,
                )
                async with aiofiles.open(segment_audio, "rb") as audio:
                    segment = await audio.read()
            else:
                start_byte = detect_lang_offset * 16000 * 2
                length_bytes = detect_lang_length * 16000 * 2
                segment = (
                    content[start_byte : start_byte + length_bytes]
                    if len(content) > start_byte
                    else content[:length_bytes]
                )

            result, _ = await TranscriptionService.transcribe_audio_data(
                audio_data=segment,
                language=candidate_locales[0],
                source=JobSource.BAZARR,
                file_name=video_file or audio_file.filename or "language detection",
                is_raw_pcm=not encode,
                candidate_locales=candidate_locales,
            )
            detected_language = LanguageCode.from_string(result.language or "")
            if detected_language == LanguageCode.NONE and result.language:
                detected_language = LanguageCode.from_string(
                    result.language.split("-")[0]
                )
            language_code = detected_language.to_iso_639_1()
            logger.info(
                f"Language detection complete: {detected_language.to_name()} ({language_code})"
            )
            return JSONResponse(
                {
                    "detected_language": (
                        detected_language.to_name()
                        if detected_language != LanguageCode.NONE
                        else "Unknown"
                    ),
                    "language_code": (
                        language_code
                        if detected_language != LanguageCode.NONE
                        else "und"
                    ),
                }
            ).body
        finally:
            if segment_audio:
                try:
                    await asyncio.to_thread(Path(segment_audio).unlink, missing_ok=True)
                except OSError as error:
                    logger.warning(
                        f"Failed to clean up language-detection segment: {error}"
                    )
            if temp_dir:
                try:
                    await asyncio.to_thread(shutil.rmtree, temp_dir)
                except OSError as error:
                    logger.warning(
                        f"Failed to clean up language-detection directory: {error}"
                    )

    return await _asr_response(
        detect,
        media_type="application/json",
        keepalive_interval=settings.asr_keepalive_interval,
        timeout=settings.transcription_timeout,
    )


def _srt_to_vtt(srt_content: str) -> str:
    """Convert SRT format to WebVTT format."""
    lines = srt_content.strip().split("\n")
    vtt_lines = ["WEBVTT", ""]

    for line in lines:
        # Replace comma with dot in timestamps
        if " --> " in line:
            line = line.replace(",", ".")
        vtt_lines.append(line)

    return "\n".join(vtt_lines)
