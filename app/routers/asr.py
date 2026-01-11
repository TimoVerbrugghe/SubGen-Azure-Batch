"""
ASR Router - Bazarr-compatible transcription endpoint.

Provides the /asr and /detect-language endpoints that mimic the Whisper ASR API format,
allowing Bazarr to use SubGen-Azure-Batch as a transcription provider.
"""

import logging
import os
import random
import shutil
import string
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional, Union

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.audio_extractor import extract_audio_segment
from app.azure_batch_transcriber import AzureBatchTranscriber
from app.config import (SUBGEN_AZURE_BATCH_VERSION, get_settings,
                        require_azure_configured)
from app.language_code import LanguageCode
from app.transcription_service import JobSource, TranscriptionService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ASR"])


@router.get("/")
async def get_root_version():
    """
    Return version info for Bazarr compatibility at root.
    Mimics whisper-asr-webservice format.
    """
    return f"Whisper ASR Webservice {SUBGEN_AZURE_BATCH_VERSION} (SubGen-Azure-Batch)"


@router.get("/asr")
async def get_asr_version():
    """
    Return error message for GET requests (matches original subgen behavior).
    
    The actual transcription happens via POST /asr.
    """
    return ["You accessed this request incorrectly via a GET request.  See https://github.com/TimoVerbrugghe/subgen-azure-batch for proper configuration"]


@router.get("/status")
async def get_status():
    """
    Return version info for Bazarr compatibility.
    
    Bazarr checks this endpoint to verify the ASR provider is working and get version info.
    Matches the original subgen format: {"version": "Subgen X.Y.Z, ..."}
    """
    return {"version": f"SubGen-Azure-Batch {SUBGEN_AZURE_BATCH_VERSION}, Azure Batch Transcription API"}


@router.post("/asr")
async def transcribe_audio(
    audio_file: UploadFile = File(...),
    task: Union[str, None] = Query(default="transcribe", enum=["transcribe", "translate"]),
    language: Union[str, None] = Query(default=None),
    video_file: Union[str, None] = Query(default=None),  # For logging (from Bazarr)
    initial_prompt: Union[str, None] = Query(default=None),  # Not used, kept for compatibility
    encode: bool = Query(default=True, description="Encode audio first through ffmpeg"),
    output: Union[str, None] = Query(default="srt", enum=["txt", "vtt", "srt", "tsv", "json"]),
    word_timestamps: bool = Query(default=False, description="Word-level timestamps"),
):
    """
    Transcribe audio file using Azure Batch Transcription.
    
    This endpoint is compatible with Whisper ASR API format used by Bazarr.
    
    Args:
        audio_file: Audio file to transcribe (WAV, MP3, etc.)
        task: Task type ('transcribe' or 'translate'). Only 'transcribe' is supported.
        language: Language code (e.g., 'en', 'es', 'de'). If None, auto-detect.
        video_file: Original video file path (for logging purposes).
        initial_prompt: Initial prompt (not used, kept for API compatibility).
        encode: Whether to encode the response. Default True.
        output: Output format ('srt', 'vtt', 'txt'). Default is 'srt'.
        word_timestamps: Whether to include word-level timestamps. Default False.
    
    Returns:
        Transcription in requested format (SRT by default).
    
    Raises:
        HTTPException: If transcription fails or configuration is invalid.
    """
    settings = get_settings()
    
    # Log the request with video file info if available
    if video_file:
        logger.info(f"Transcribe request for '{video_file}' from Bazarr/ASR webhook")
    else:
        logger.info("Transcribe request from Bazarr/ASR webhook")
    
    # Validate Azure configuration
    require_azure_configured()
    
    # Only transcription is supported (no translation via Azure Batch API)
    if task == "translate":
        logger.warning("Translation task requested but not supported by Azure Batch API, falling back to transcribe")
    
    # Determine output format
    output_format = (output or "srt").lower()
    
    try:
        # Read uploaded file content
        content = await audio_file.read()
        logger.debug(f"Received audio data: {len(content)} bytes, encode={encode}")
        
        # Use the unified TranscriptionService
        # The service handles: audio compression, Azure upload, transcription, cleanup
        result, job = await TranscriptionService.transcribe_audio_data(
            audio_data=content,
            language=language or "en",
            source=JobSource.BAZARR,
            file_name=video_file or audio_file.filename or "unknown",
            is_raw_pcm=not encode,  # If encode=False, it's raw PCM
        )
        
        # Generate output in requested format
        if output_format == "srt":
            output_content = result.to_srt()
        elif output_format == "vtt":
            output_content = _srt_to_vtt(result.to_srt())
        elif output_format == "txt":
            output_content = result.text
        else:
            output_content = result.to_srt()
        
        # Return as streaming response with source header (like original subgen)
        return StreamingResponse(
            iter([output_content]),
            media_type="text/plain",
            headers={
                'Source': 'Transcribed using Azure Batch API from SubGen-Azure-Batch!',
            }
        )
            
    except Exception as e:
        error_msg = f"Error processing Bazarr file: {video_file}" if video_file else "Error processing Bazarr file"
        logger.exception(f"{error_msg} -- Exception: {e}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    
    finally:
        # Cleanup temp files
        await audio_file.close()


@router.get("/asr/languages")
async def list_languages():
    """
    List supported languages for transcription.
    
    Returns:
        List of supported language codes with their names.
    """
    languages = []
    for lang in LanguageCode:
        languages.append({
            "code": lang.value,
            "name": lang.name.replace("_", " ").title(),
            "azure_locale": lang.to_azure_locale()
        })
    return {"languages": languages}


@router.get("/detect-language")
async def get_detect_language_version():
    """
    Return error message for GET requests (matches original subgen behavior).
    
    The actual language detection happens via POST /detect-language.
    """
    return ["You accessed this request incorrectly via a GET request.  See https://github.com/TimoVerbrugghe/subgen-azure-batch for proper configuration"]


@router.post("//detect-language")
@router.post("/detect-language")
async def detect_language(
    audio_file: UploadFile = File(...),
    encode: bool = Query(default=True, description="Encode audio first through ffmpeg"),
    video_file: Union[str, None] = Query(default=None),
    detect_lang_length: Optional[int] = Query(default=None, description="Detect language on X seconds of the file"),
    detect_lang_offset: Optional[int] = Query(default=None, description="Start detect language X seconds into the file"),
):
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
        logger.info(f"Detecting language for file '{video_file}' from Bazarr/detect-language webhook")
    else:
        logger.info("Detecting language from Bazarr/detect-language webhook")
    
    # Check for forced language setting (read from environment)
    force_language = os.getenv('FORCE_DETECTED_LANGUAGE_TO', '')
    if force_language:
        lang_code = LanguageCode.from_string(force_language)
        if lang_code != LanguageCode.NONE:
            logger.debug(f"Skipping detect language, forced to {lang_code.to_name()}")
            return {
                "detected_language": lang_code.to_name(),
                "language_code": lang_code.to_iso_639_1()
            }
    
    # Validate Azure configuration
    require_azure_configured()
    
    # Save uploaded file to temp location
    temp_dir = tempfile.mkdtemp(prefix="subgen_detect_")
    segment_audio: Optional[str] = None
    detected_language = LanguageCode.NONE
    language_code = 'und'
    
    try:
        # Read uploaded file content
        content = await audio_file.read()
        logger.debug(f"Received audio data: {len(content)} bytes, encode={encode}")
        
        if encode:
            # Audio is in a proper file format (WAV, MP3, etc.) - save and extract segment
            temp_input = Path(temp_dir) / (audio_file.filename or "audio.wav")
            temp_input.write_bytes(content)
            logger.debug(f"Saved encoded audio to {temp_input}")
            
            # Extract a short segment for language detection
            segment_audio = await extract_audio_segment(
                str(temp_input),
                offset=float(detect_lang_offset),
                duration=float(detect_lang_length),
                output_format='wav',
                sample_rate=16000
            )
        else:
            # Audio is raw PCM data (16-bit, 16kHz, mono) - need to wrap in WAV container
            # Bazarr sends raw PCM when encode=false
            sample_rate = 16000
            bytes_per_sample = 2  # 16-bit = 2 bytes
            channels = 1
            
            # Calculate how many samples to extract
            start_byte = detect_lang_offset * sample_rate * bytes_per_sample
            length_bytes = detect_lang_length * sample_rate * bytes_per_sample
            
            # Extract the segment from raw PCM
            if len(content) > start_byte:
                pcm_segment = content[start_byte:start_byte + length_bytes]
            else:
                pcm_segment = content[:length_bytes]  # Use what we have
            
            logger.debug(f"Extracted PCM segment: {len(pcm_segment)} bytes from raw data")
            
            # Create WAV file from raw PCM data
            segment_audio = os.path.join(temp_dir, "segment.wav")
            with wave.open(segment_audio, 'wb') as wav_file:
                wav_file.setnchannels(channels)
                wav_file.setsampwidth(bytes_per_sample)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_segment)
            
            logger.debug(f"Created WAV file from raw PCM: {segment_audio}")
        
        logger.debug(f"Audio segment ready for language detection: {segment_audio}")
        
        # Create transcriber and process
        transcriber = AzureBatchTranscriber()
        blob_name_to_cleanup: Optional[str] = None
        job_id_to_cleanup: Optional[str] = None
        
        try:
            # Upload segment to blob storage
            audio_url, blob_name = await transcriber.upload_audio(segment_audio)
            blob_name_to_cleanup = blob_name
            logger.debug(f"Uploaded audio segment to Azure Blob Storage: {blob_name}")
            
            # Create a short transcription job with language auto-detection
            # We use a multi-language locale to enable auto-detection
            random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            job = await transcriber.create_transcription(
                audio_url=audio_url,
                locale="en-US",  # Default, but we'll detect from result
                display_name=f"detect-lang-{random_suffix}"
            )
            job_id_to_cleanup = job.id
            logger.debug(f"Created language detection transcription job: {job.id}")
            
            # Wait for completion
            result = await transcriber.wait_for_transcription(job.id)
            
            # Extract detected language from result
            if result.language:
                # Azure returns the language in the result
                detected_language = LanguageCode.from_string(result.language)
                if detected_language == LanguageCode.NONE:
                    # Try parsing as Azure locale (e.g., "en-US")
                    lang_part = result.language.split('-')[0] if '-' in result.language else result.language
                    detected_language = LanguageCode.from_string(lang_part)
                
                language_code = detected_language.to_iso_639_1() if detected_language != LanguageCode.NONE else 'und'
            
            logger.info(f"Language detection complete: {detected_language.to_name()} ({language_code})")
            
        finally:
            # Cleanup Azure resources
            if blob_name_to_cleanup:
                try:
                    await transcriber.delete_blob(blob_name_to_cleanup)
                    logger.debug(f"Cleaned up blob: {blob_name_to_cleanup}")
                except Exception as e:
                    logger.warning(f"Failed to delete blob {blob_name_to_cleanup}: {e}")
            
            if job_id_to_cleanup:
                try:
                    await transcriber.delete_transcription(job_id_to_cleanup)
                    logger.debug(f"Cleaned up transcription job: {job_id_to_cleanup}")
                except Exception as e:
                    logger.warning(f"Failed to delete transcription job {job_id_to_cleanup}: {e}")
            
            await transcriber.close()
            
    except Exception as e:
        error_msg = f"Error detecting language for Bazarr file: {video_file}" if video_file else "Error detecting language for Bazarr file"
        logger.exception(f"{error_msg} -- Exception: {e}")
        # Return undetermined rather than throwing - Bazarr may still work with default language
        return {
            "detected_language": LanguageCode.NONE.to_name() or "Unknown",
            "language_code": "und"
        }
    
    finally:
        # Cleanup temp files
        await audio_file.close()
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")
        
        # Cleanup segment audio
        if segment_audio:
            try:
                os.unlink(segment_audio)
            except Exception:
                pass
    
    return {
        "detected_language": detected_language.to_name() if detected_language != LanguageCode.NONE else "Unknown",
        "language_code": language_code
    }


def _srt_to_vtt(srt_content: str) -> str:
    """Convert SRT format to WebVTT format."""
    lines = srt_content.strip().split('\n')
    vtt_lines = ["WEBVTT", ""]
    
    for line in lines:
        # Replace comma with dot in timestamps
        if ' --> ' in line:
            line = line.replace(',', '.')
        vtt_lines.append(line)
    
    return '\n'.join(vtt_lines)
