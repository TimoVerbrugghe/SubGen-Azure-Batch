# SubGen-Azure-Batch - Cloud-Based Subtitle Generator

## Project Overview

SubGen-Azure-Batch is a cloud-based automatic subtitle generation service that uses the **Azure Batch Transcription API** to transcribe audio/video files into SRT subtitle files. It provides a web UI for batch processing and integrates with Plex, Jellyfin, Emby, and Bazarr.

**Key Difference from Original Subgen**: This is a fork of [SubGen](https://github.com/McCloudS/subgen) that does NOT run Whisper locally. All transcription is performed via Azure Speech Services Batch Transcription API, making it a pure cloud-based solution with no GPU requirements.

## Tech Stack

- **Language**: Python 3.10+
- **Web Framework**: FastAPI with Uvicorn
- **Frontend**: HTML/CSS/JavaScript (embedded in FastAPI, no separate build)
- **Transcription**: Azure Speech Services Batch Transcription API
- **Audio Processing**: FFmpeg (for audio extraction)
- **Containerization**: Docker (lightweight, no CUDA needed)

## Core Features

1. **Web UI**: Browse media folders, multi-select files, submit for batch transcription
2. **Azure Batch Transcription**: Uses Azure Speech Services for cloud-based transcription
3. **Bazarr Integration**: Exposes Whisper-compatible endpoint that uses Azure backend
4. **Webhook Integration**: Receives webhooks from Plex, Jellyfin, Emby, Tautulli
5. **Multi-language Support**: Supports 90+ languages via Azure Speech Services
6. **Bazarr Sync**: Triggers Bazarr disk scan after subtitle generation
7. **Job Tracking**: Monitor transcription job status in the UI
8. **Pushover Notifications**: Get notified when transcription jobs fail

---

## File Structure

```text
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application entry point
│   ├── config.py                   # Environment variables + helper utilities
│   ├── transcription_service.py    # Unified transcription orchestration (all sources)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── webhooks.py             # Plex, Jellyfin, Emby, Tautulli endpoints
│   │   ├── asr.py                  # Bazarr-compatible whisper endpoint
│   │   ├── batch.py                # Batch transcription endpoints
│   │   └── ui.py                   # Web UI routes
│   ├── utils/                      # Utility modules (imported by TranscriptionService)
│   │   ├── __init__.py             # Re-exports all utilities for convenience
│   │   ├── audio_extractor.py      # FFmpeg audio extraction utilities
│   │   ├── azure_batch_transcriber.py  # Azure Batch Transcription API client
│   │   ├── bazarr_client.py        # Bazarr API integration
│   │   ├── language_code.py        # ISO 639 language code definitions
│   │   ├── media_server_client.py  # Plex/Jellyfin/Emby API clients
│   │   ├── notification_service.py # Failure notifications (Pushover)
│   │   ├── skip_checker.py         # Skip logic for existing subtitles
│   │   └── subtitle_utils.py       # SRT/LRC file generation and manipulation
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   ├── js/
│   │   │   └── app.js
│   │   └── images/
│   │       ├── icon-black.png      # Logo for light theme
│   │       └── icon-white.png      # Logo for dark theme
│   └── templates/
│       └── index.html              # Main web UI template
├── tests/
│   ├── __init__.py
│   ├── .env                        # Test environment variables (gitignored)
│   ├── conftest.py                 # Pytest fixtures
│   ├── test_*.py                   # Unit tests for each module
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Lightweight Docker image (no CUDA)
├── docker-compose.yml              # Docker Compose for SubGen-Azure-Batch
├── .env.example                    # Environment variable template
├── LICENSE                         # MIT License
├── README.md                       # Main documentation
└── CLAUDE.md                       # This file - AI assistant context
```

---

## Module Organization & Single Responsibility

Each module has a clear, single responsibility to maintain clean architecture and avoid code duplication:

### Core Modules (app/)

| Module | Responsibility | Key Functions |
| ------ | -------------- | ------------- |
| `config.py` | Environment variables + helper utilities | `require_azure_configured()`, `format_duration()`, `SkipConfig` |
| `transcription_service.py` | Unified transcription orchestration | Session/job management for UI and Bazarr sources |

### Utility Modules (app/utils/)

All utility modules are in `app/utils/` and can be imported via `from app.utils import ...`:

| Module | Responsibility | Key Functions |
| ------ | -------------- | ------------- |
| `azure_batch_transcriber.py` | Azure Speech API client | `create_transcription()`, `wait_for_completion()`, `to_srt()` |
| `audio_extractor.py` | FFmpeg audio extraction | `extract_audio()` - video to OGG/Opus conversion |
| `subtitle_utils.py` | SRT/LRC file utilities | `seconds_to_srt_time()`, `get_srt_path()`, `save_srt()`, `save_lrc()` |
| `bazarr_client.py` | Bazarr API integration | `trigger_series_scan()`, `trigger_movie_scan()`, `notify_bazarr_of_new_subtitle()` |
| `media_server_client.py` | Plex/Jellyfin/Emby API clients | `refresh_metadata()`, `refresh_by_file_path()` |
| `skip_checker.py` | Skip logic for subtitle generation | `should_skip_file()`, `get_stream_info()` via ffprobe |
| `notification_service.py` | Failure notifications (Pushover) | `notify_failure()`, `NotificationService` singleton |
| `language_code.py` | Language code mappings | ISO 639 codes, Azure locale conversion |

---

## Key Design Decisions

1. **TranscriptionService as Central Orchestrator**: All transcription requests (UI batch, Bazarr ASR, webhooks) flow through a single `TranscriptionService` class. This ensures:
   - Unified session/job tracking visible in the web UI
   - Consistent logging and error handling
   - Single point for Azure API interaction

2. **Helper Functions in config.py**: Common utilities live in config.py to avoid duplication:
   - `require_azure_configured()` - Raises HTTP 503 if Azure credentials missing
   - `format_duration(seconds)` - Formats duration as "X minutes and Y seconds"

3. **Canonical SRT Utilities in subtitle_utils.py**: All SRT-related functions consolidated here:
   - `seconds_to_srt_time()` - Formats seconds as `HH:MM:SS,mmm`
   - `get_srt_path()` - Generates subtitle path from media path + language (respects naming config)
   - `save_srt()` - Saves SRT content with proper language formatting
   - `format_language_for_filename()` - Formats language code per `SUBTITLE_LANGUAGE_NAMING_TYPE`
   - `generate_srt()` - Creates SRT content from transcription segments

4. **Session Source Tracking**: Jobs are tagged with their source (`Bazarr` or `Batch`) for UI visibility with color-coded badges

5. **Global Concurrency Limit with Priority Queue**: The `CONCURRENT_TRANSCRIPTIONS` limit is enforced globally:
   - All transcription jobs share a global semaphore regardless of source
   - Bazarr requests get priority and jump ahead of queued batch jobs
   - This ensures Bazarr users don't have to wait for large batch jobs to complete
   - Implemented in `TranscriptionService.acquire_transcription_slot(priority=True/False)`

6. **Skip Checker (skip_checker.py)**: Uses FFprobe (not pyav) for stream inspection:
   - `should_skip_file()` - Async function checking all skip conditions
   - `get_stream_info()` - FFprobe-based audio/subtitle stream extraction
   - `SkipConfig` in config.py - Centralized skip configuration via env vars

7. **Subtitle Naming (SubtitleNamingConfig)**: Controls filename language format:
   - `SUBTITLE_LANGUAGE_NAMING_TYPE` - Format type: ISO_639_1, ISO_639_2_T, ISO_639_2_B, NAME, NATIVE
   - `SHOW_SUBGEN_MARKER` - Include `.subgen` in filename for identification

8. **Media Server Refresh**: After subtitle creation, triggers metadata refresh:
   - **Webhooks**: Item IDs passed to background task, refresh triggered AFTER transcription completes
   - **UI Batch**: Uses `refresh_by_file_path()` to search and refresh (no item ID available)
   - **Bazarr ASR**: No refresh (Bazarr handles its own media library updates)

9. **Bazarr Notifications**: Triggers Bazarr disk scan after subtitle generation:
   - Uses `PATCH /api/series?seriesid=X&action=scan-disk` for TV shows
   - Uses `PATCH /api/movies?radarrid=X&action=scan-disk` for movies
   - Smart path-based lookup to find Sonarr/Radarr IDs from file paths

---

## Key Environment Variables

| Variable | Default | Description |
| -------- | ------- | ----------- |
| `AZURE_SPEECH_KEY` | `` | Azure Speech Services API key (required) |
| `AZURE_SPEECH_REGION` | `swedencentral` | Azure region for Speech Services |
| `AZURE_STORAGE_CONNECTION_STRING` | `` | Azure Blob Storage connection string (for audio upload) |
| `AZURE_STORAGE_CONTAINER` | `transcription-audio` | Container name for audio files |
| `WEBHOOK_PORT` | `9000` | Port for webhook server |
| `UVICORN_TIMEOUT_KEEP_ALIVE` | (unset) | TCP keepalive timeout in seconds. Set to prevent connection resets during long transcriptions. |
| `MEDIA_FOLDERS` | `/tv,/movies` | Comma-separated list of media folders to browse |
| `SUBTITLE_LANGUAGE` | `en` | Default language for transcription |
| `BAZARR_URL` | `` | Bazarr server URL (optional) |
| `BAZARR_API_KEY` | `` | Bazarr API key (optional) |
| `PLEX_TOKEN` | `` | Plex authentication token |
| `PLEX_SERVER` | `` | Plex server URL |
| `JELLYFIN_TOKEN` | `` | Jellyfin authentication token |
| `JELLYFIN_SERVER` | `` | Jellyfin server URL |
| `EMBY_TOKEN` | `` | Emby authentication token |
| `EMBY_SERVER` | `` | Emby server URL |
| `CONCURRENT_TRANSCRIPTIONS` | `50` | Global maximum concurrent transcription jobs (enforced across all sessions) |
| `TRANSCODE_DIR` | `` | Directory for temp audio files (mount a volume to reduce memory usage) |
| `SKIP_IF_TARGET_SUBTITLES_EXIST` | `true` | Skip if target language subtitle exists |
| `SKIP_IF_EXTERNAL_SUBTITLES_EXIST` | `false` | Skip if any external subtitle exists |
| `SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE` | `` | Skip if internal subs in language (e.g., `en`) |
| `SKIP_ONLY_SUBGEN_SUBTITLES` | `false` | Only skip if `.subgen.` in filename |
| `SKIP_IF_AUDIO_TRACK_IS` | `` | Skip if audio track in languages (e.g., `en\|eng`) |
| `SKIP_SUBTITLE_LANGUAGES` | `` | Skip if any subtitle (internal/external) in languages |
| `SKIP_UNKNOWN_LANGUAGE` | `false` | Skip if audio has unknown/undefined language |
| `SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST` | `false` | Skip if no audio language but subtitles exist |
| `SUBTITLE_LANGUAGE_NAMING_TYPE` | `ISO_639_2_B` | Language format in filename |
| `SUBTITLE_LANGUAGE_NAME` | `` | Override detected language in filename |
| `SHOW_IN_SUBNAME_SUBGEN` | `false` | Include `.subgen` in subtitle filename |
| `FORCE_DETECTED_LANGUAGE_TO` | `` | Force all transcriptions to this language |
| `APPEND` | `false` | Append "Transcribed by SubGen-Azure-Batch" credit line |
| `LRC_FOR_AUDIO_FILES` | `true` | Write .lrc files for audio files instead of .srt |
| `PREFERRED_AUDIO_LANGUAGES` | `eng` | Preferred audio track languages (pipe-separated) |
| `LIMIT_TO_PREFERRED_AUDIO_LANGUAGE` | `false` | Skip files without preferred audio track |
| `DETECT_LANGUAGE_LENGTH` | `30` | Detect language on first x seconds of audio |
| `DETECT_LANGUAGE_OFFSET` | `0` | Start language detection x seconds into file |
| `PUSHOVER_USER_KEY` | `` | Pushover user key for failure notifications |
| `PUSHOVER_API_TOKEN` | `` | Pushover API token for failure notifications |
| `NOTIFY_ON_FAILURE` | `true` | Send notification when transcription fails |

---

## API Endpoints

### Web UI

- `GET /` - Main web interface for browsing and batch transcription

### Batch Processing

- `GET /api/files` - List files in configured media folders
- `POST /api/batch/submit` - Submit files for batch transcription
- `GET /api/batch/status/{job_id}` - Get status of a transcription job
- `GET /api/batch/jobs` - List all transcription jobs

### Bazarr Compatible (Whisper Provider)

- `POST /asr` - ASR endpoint compatible with Bazarr's Whisper provider (returns SRT)
- `GET /detect-language` - Language detection endpoint (Bazarr compatibility)
- `POST /detect-language` - Language detection with audio upload
- `GET /status` - Health check / version status

### Media Server Webhooks

- `POST /plex` - Plex webhook receiver
- `POST /jellyfin` - Jellyfin webhook receiver
- `POST /emby` - Emby webhook receiver
- `POST /tautulli` - Tautulli webhook receiver

### Notifications

- `POST /api/notifications/test` - Send a test Pushover notification

---

## Azure Batch Transcription API

### How It Works

1. **Audio Extraction**: Extract audio from video files using FFmpeg (OGG/Opus at 64kbps)
2. **Upload to Azure Blob**: Upload audio to Azure Blob Storage (required by Batch API)
3. **Create Transcription Job**: Submit batch transcription request to Azure
4. **Poll for Completion**: Monitor job status until complete
5. **Download Results**: Retrieve transcription results
6. **Generate SRT**: Convert results to SRT format
7. **Save & Notify**: Save SRT file and trigger Bazarr/media server refresh

### API Limits

- **Max audio duration**: 10 hours per file
- **Max concurrent jobs**: Varies by tier (default: 20)
- **Max files per job**: 1000
- **Supported formats**: WAV, MP3, OGG, FLAC, WMA, AAC, AIFF, MP4, MKV, etc.
- **Pricing**: ~$1.00 per hour of audio (standard tier)

### API Reference

```http
POST https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2/transcriptions
Ocp-Apim-Subscription-Key: {key}
Content-Type: application/json

{
  "contentUrls": ["https://storage.blob.core.windows.net/audio/file.wav?sastoken"],
  "locale": "en-US",
  "displayName": "My Transcription",
  "properties": {
    "wordLevelTimestampsEnabled": true,
    "displayFormWordLevelTimestampsEnabled": true,
    "diarizationEnabled": false,
    "punctuationMode": "DictatedAndAutomatic",
    "profanityFilterMode": "None"
  }
}
```

---

## Differences from Original SubGen

| Feature | Original SubGen | SubGen-Azure-Batch |
|---------|----------------|--------------|
| **Transcription Engine** | Local Whisper (faster-whisper + stable-ts) | Azure Batch Transcription API |
| **GPU Required** | Yes (for reasonable speed) | No |
| **Docker Image Size** | ~8GB (CUDA) | ~500MB |
| **Processing Style** | Real-time, synchronous | Batch (asynchronous) |
| **Cost Model** | Hardware investment | Pay-per-use (Azure) |
| **Web UI** | Removed in recent versions | Included |
| **Standalone Mode** | Yes (launcher.py) | Docker only |

### Features NOT Ported (Not Applicable to Cloud Architecture)

- `TRANSCRIBE_DEVICE` - No local GPU/CPU selection
- `WHISPER_MODEL` - Azure uses its own models
- `WHISPER_THREADS` - No local threading
- `MODEL_PATH` - No local model storage
- `COMPUTE_TYPE` - No local quantization
- `CLEAR_VRAM_ON_COMPLETE` - No local VRAM
- `MONITOR` - Folder monitoring not implemented
- `TRANSCRIBE_FOLDERS` - Use Web UI batch feature
- `HF_TRANSFORMERS` / `HF_BATCH_SIZE` - No Hugging Face
- `USE_MODEL_PROMPT` / `CUSTOM_MODEL_PROMPT` - Azure handles prompting
- `CUSTOM_REGROUP` - Azure handles subtitle grouping
- `WORD_LEVEL_HIGHLIGHT` - Not supported by Azure
- `SUBGEN_KWARGS` - No local Whisper kwargs
- `TRANSCRIBE_OR_TRANSLATE` - Azure only transcribes
- `PLEX_QUEUE_*` - Use Web UI batch instead
- `SHOW_IN_SUBNAME_MODEL` - No model name (Azure)
- Apprise notifications - Use Pushover instead

---

## Bazarr Integration

SubGen-Azure-Batch acts as a WhisperAI provider for Bazarr:

### Configuration in Bazarr

- **Provider**: WhisperAI
- **Endpoint**: `http://subgen-azure-batch:9000`
- **Timeout**: Connection 5s, Read 3600s (Azure transcription can take minutes)

### Language Handling

- Bazarr sends ISO 639-1 codes (`en`, `de`, `fr`)
- Azure requires full locales (`en-US`, `de-DE`, `fr-FR`)
- `language_code.py` handles conversion with sensible defaults

---

## Running the Application

### Docker (Recommended)

```bash
# Build and run
docker compose up -d

# View logs
docker compose logs -f
```

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables (or create .env file)
export AZURE_SPEECH_KEY=your-key
export AZURE_SPEECH_REGION=swedencentral
export AZURE_STORAGE_CONNECTION_STRING=your-connection-string

# Run the app
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
```

### Testing

```bash
# Set up test environment
cp tests/.env.example tests/.env
# Edit tests/.env with your Azure credentials

# Run tests
pytest tests/ -v
```

---

## Credits

- Original [SubGen](https://github.com/McCloudS/subgen) by [McCloudS](https://github.com/McCloudS)
- [Azure Speech Services](https://azure.microsoft.com/services/cognitive-services/speech-services/)
- [FFmpeg](https://ffmpeg.org/)
- [Whisper ASR Webservice](https://github.com/ahmetoner/whisper-asr-webservice) - Bazarr implementation reference
