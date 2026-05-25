# AGENTS.md — SubGen-Azure-Batch

## Project Overview

SubGen-Azure-Batch is a cloud-based automatic subtitle generation service that uses the **Azure Batch Transcription API** to transcribe audio/video files into SRT subtitle files. It provides a web UI for batch processing and integrates with Plex, Jellyfin, Emby, and Bazarr.

**This is NOT a local-Whisper project.** All transcription is performed via Azure Speech Services Batch Transcription API — no GPU required, no local models. Read `CLAUDE.md` for full project background.

Key files to read first:
- `requirements.txt` — Python dependencies
- `.env.example` — all environment variables with documentation
- `Dockerfile` — runtime: `python:3.11-slim` + FFmpeg system dep

## Repository Structure

```
├── app/
│   ├── main.py                     # FastAPI app entry point, lifespan, middleware
│   ├── config.py                   # All env vars as dataclasses via get_settings()
│   ├── transcription_service.py    # Central orchestrator for all transcription sources
│   ├── routers/
│   │   ├── asr.py                  # Bazarr-compatible /asr and /detect-language endpoints
│   │   ├── batch.py                # Batch processing /api/batch/* endpoints
│   │   ├── ui.py                   # Web UI routes
│   │   └── webhooks.py             # Plex/Jellyfin/Emby/Tautulli webhook receivers
│   ├── utils/
│   │   ├── audio_extractor.py      # FFmpeg: video/audio → OGG/Opus
│   │   ├── azure_batch_transcriber.py  # Azure Speech Batch Transcription API client
│   │   ├── bazarr_client.py        # Bazarr API: scan-disk after subtitle creation
│   │   ├── language_code.py        # ISO 639 ↔ Azure locale conversion
│   │   ├── media_server_client.py  # Plex/Jellyfin/Emby metadata refresh
│   │   ├── notification_service.py # Pushover failure notifications
│   │   ├── skip_checker.py         # FFprobe-based skip logic
│   │   └── subtitle_utils.py       # SRT/LRC file generation and path helpers
│   ├── static/                     # CSS, JS, images for Web UI
│   └── templates/index.html        # Jinja2 Web UI template
├── tests/
│   ├── conftest.py                 # pytest fixtures, markers, mock_settings
│   └── test_*.py                   # One test file per app module
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Tech Stack

| Component | Detail |
|-----------|--------|
| Runtime | Python 3.10–3.12 (Dockerfile pins 3.11) |
| Web framework | FastAPI + Uvicorn |
| Transcription | Azure Batch Transcription API v3.2 |
| Storage | Azure Blob Storage (audio upload required before batch transcription) |
| Audio extraction | FFmpeg (system dependency, installed in Docker) |
| Testing | pytest + pytest-asyncio |
| Linting | ruff (check + format) |
| Container | Docker multi-arch (linux/amd64, linux/arm64) — no GPU/CUDA |

## Build & Run

```bash
# Local development
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload

# Docker (recommended)
docker compose up -d
docker compose logs -f

# Run tests (unit tests only — excludes Azure API calls)
pytest tests/ -v --tb=short -m "not azure_api and not integration and not slow"

# Run tests with coverage
pytest tests/ -v --cov=app --cov-report=term-missing -m "not azure_api and not integration and not slow"

# Lint
ruff check app/ tests/
ruff format --check app/ tests/
```

Copy `.env.example` to `.env` and fill in `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, and `AZURE_STORAGE_CONNECTION_STRING` before running locally.

## Testing

- **Framework**: pytest + pytest-asyncio
- **Test directory**: `tests/`
- **Markers** (defined in `tests/conftest.py`):
  - `@pytest.mark.azure_api` — requires real Azure credentials; **always excluded in CI**
  - `@pytest.mark.integration` — requires running external services
  - `@pytest.mark.slow` — long-running tests
- **Fixtures**: use `mock_settings` / `patched_settings` from `conftest.py` for all unit tests
- **Coverage**: run with `--cov=app --cov-report=term-missing`

Each module in `app/` has a corresponding `tests/test_<module>.py`.

## Key Patterns and Conventions

### All transcription flows through TranscriptionService
Whether triggered from Web UI, Bazarr ASR, or a webhook — all transcription goes through `app/transcription_service.py`. This provides unified session/job tracking in the UI. **Never call `AzureBatchTranscriber` directly from a router.**

### Settings via `get_settings()`
All environment variables are accessed through `app.config.get_settings()` which returns a cached `Settings` dataclass. **Never call `os.getenv()` directly in routers or utilities.**

### `require_azure_configured()` guard
Every router endpoint that calls Azure must call `require_azure_configured()` from `app.config` first. It raises HTTP 503 if credentials are missing. Do not duplicate this check inline.

### Subtitle file paths via `subtitle_utils.get_srt_path()`
Never construct subtitle paths manually. Always use `get_srt_path(media_path, language)` from `app.utils.subtitle_utils`. It respects `SUBTITLE_LANGUAGE_NAMING_TYPE` and `SHOW_IN_SUBNAME_SUBGEN`.

### Language codes via `language_code.py`
Never hardcode Azure locale strings like `"en-US"`. Use `get_azure_locale(language_code)` from `app.utils.language_code` to convert ISO 639-1 codes to Azure locales.

### Skip logic via `skip_checker.py`
Never duplicate skip condition checks in routers. Always call `should_skip_file(file_path)` from `app.utils.skip_checker`. It reads all skip config from `SkipConfig` in `app.config`.

### Background tasks for webhook transcription
Webhook routers (Plex/Jellyfin/Emby/Tautulli) must return 200 immediately and offload transcription to a `BackgroundTasks` task. Media server metadata refresh is triggered **after** transcription completes, using the item ID from the webhook payload.

## CI/CD

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| Tests | `.github/workflows/tests.yml` | PR + push to `main` | pytest on Python 3.10/3.11/3.12 + ruff lint |
| Docker publish | `.github/workflows/docker-publish.yml` | Push to `main` (app changes) | Build + push multi-arch image to GHCR |

Azure API tests are excluded from CI via `-m "not azure_api and not integration and not slow"`.

## Adding a New API Endpoint

Full registration chain — complete all steps:

1. **Create or update a router** in `app/routers/<name>.py`
2. **Export the router** from `app/routers/__init__.py`
3. **Register** in `app/main.py` via `app.include_router(<name>_router)`
4. **Guard** with `require_azure_configured()` at the top of any Azure-calling endpoint
5. **Use `get_settings()`** — never `os.getenv()` — for all config access
6. **Add tests** in `tests/test_routers.py` using the `app_client` fixture
7. **Update** `README.md` API Endpoints table

## Adding a New Utility Module

1. Create `app/utils/<name>.py` with a module docstring
2. Export public functions from `app/utils/__init__.py`
3. Add test file `tests/test_<name>.py`

## Common Pitfalls

- **Don't use `os.getenv()` in routers/utils** — use `get_settings()` instead
- **Don't call `AzureBatchTranscriber` directly from routers** — route through `TranscriptionService`
- **Don't hardcode Azure locale strings** — use `language_code.get_azure_locale()`
- **Don't construct SRT paths manually** — use `subtitle_utils.get_srt_path()`
- **Azure API tests are always skipped in CI** — mark them `@pytest.mark.azure_api`
- **FFmpeg must be installed** — all audio extraction and FFprobe stream inspection requires it
- **Batch Transcription requires Azure Blob Storage** — audio must be uploaded before creating a transcription job; the API cannot accept direct file uploads
