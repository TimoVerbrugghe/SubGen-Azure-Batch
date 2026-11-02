# Copilot Instructions — SubGen-Azure-Batch

## Language: Python

- Python 3.10+ compatible syntax; avoid 3.11+ exclusive features in production paths
- Use `async`/`await` throughout — all I/O (HTTP calls, file reads, subprocess) must be async
- Use `dataclasses` for configuration objects (follow the pattern in `app/config.py`)
- Prefer `pathlib.Path` over `os.path` for file operations
- Use f-strings for string formatting
- Type hints on all function signatures

## Framework: FastAPI

- All routers live in `app/routers/` with a module-level `router = APIRouter()` instance
- Export routers from `app/routers/__init__.py`; register in `app/main.py` via `app.include_router()`
- Return Pydantic models or dicts from endpoints — never raw strings for JSON responses
- Use `BackgroundTasks` for webhook handlers so they return 200 immediately
- Guard every Azure-calling endpoint with `require_azure_configured()` at the top of the function
- Never read `os.getenv()` in routers — always use `get_settings()` from `app.config`

## Azure Integration

- Azure Batch Transcription API base URL: `https://{region}.api.cognitive.microsoft.com/speechtotext/v3.2`
- Authenticate via `Ocp-Apim-Subscription-Key` header (direct aiohttp, not Azure SDK)
- Audio must be uploaded to Azure Blob Storage before submitting a transcription job
- Language codes must be full Azure locales (`en-US`, `de-DE`) — convert from ISO 639-1 via `language_code.get_azure_locale()`
- Language identification supports max 4 candidate locales

## Media-server Refresh Behavior and Path Mapping

- Contact all configured media servers independently after each successful video subtitle save through `TranscriptionService`. Send tokens only in HTTP headers. Do not request metadata/image replacement or fall back to full-library scans.
- Do not refresh failed, cancelled, response-only `/asr`, or audio/LRC jobs. Bazarr saves `/asr` responses itself; refresh must happen after Bazarr's save, not prematurely in SubGen.
- Preserve per-server `queued`, `not_found`, and `failed` outcomes, including mixed success/failure. Queued means accepted, not that subtitle indexing has completed; refresh failures must not fail completed transcriptions.
- Refresh requests use 30-second timeouts and at most three attempts for connection failures or transient HTTP responses. Do not retry invalid credentials.
- Reuse library/path indexes within a transcription session, but repeat refresh requests after later subtitle writes: an earlier directory scan cannot discover subtitles written afterwards.
- Map each server's **SubGen-visible** root to its **server-visible** root. Set both mapping values or neither; match directory boundaries, not arbitrary string prefixes, and support POSIX and Windows server paths.

```dotenv
# SubGen sees /media/tv/Show/episode.mkv
PLEX_PATH_MAPPING_FROM=/media
PLEX_PATH_MAPPING_TO=/data
JELLYFIN_PATH_MAPPING_FROM=/media
JELLYFIN_PATH_MAPPING_TO=/mnt/library
```

- Reverse per-server mappings for incoming webhooks and retain original server paths for refresh. The legacy `USE_PATH_MAPPING` / `PATH_MAPPING_FROM` / `PATH_MAPPING_TO` maps server -> SubGen on input and is reversed for refresh when no per-server mapping is set. Both servers must read the generated subtitle beside the same underlying media file.
- Keep desktop and compact-screen Plex/Jellyfin badges consistent: green **Connected**, grey **No API key**, red **Connection failed** for a supplied key whose authentication/connectivity fails, including a missing server URL. Red does not necessarily mean an invalid key; connectivity and permissions can also fail.
- On page load, `/api/status` checks environment credentials with authenticated, read-only `GET /library/sections` for Plex and `GET /System/Info` for Jellyfin, using 10-second timeouts. Media-server `configured` means a nonblank key is supplied. Checks must not trigger scans, expose tokens, or require Azure configuration.
- Connected confirms read access, not subtitle indexing or authorization for every refresh operation. Keep failure details in badge tooltips; reload the page to recheck after configuration changes/restarts. Clients may need to reopen the subtitle selector after a queued refresh.

## Testing Conventions

- One test file per app module: `tests/test_<module_name>.py`
- Use `mock_settings` fixture from `conftest.py` for all unit tests — never set real env vars in tests
- Use `patched_settings` when the code under test calls `get_settings()` internally
- Mark tests requiring real Azure credentials with `@pytest.mark.azure_api`
- All async tests require `@pytest.mark.asyncio`
- Mock all external calls (Azure API, Plex/Jellyfin/Emby/Bazarr) with `unittest.mock.AsyncMock`
- Do not test private helper functions directly — test through the public interface

## Code Style

- Linter: `ruff check app/ tests/`
- Formatter: `ruff format app/ tests/`
- Module docstrings on all files, class docstrings on all classes, function docstrings on public functions
- Log at `INFO` for normal operations, `WARNING` for skipped files, `ERROR` for failures
- Use `logger = logging.getLogger(__name__)` at module level — never call `print()`

## PR Review Patterns

No review patterns found yet (PRs are currently self-merged). Add conventions here as the team grows.

## Maintenance Matrix

When you change these files, you **must** also update:

| Change | Also update |
|--------|-------------|
| `app/config.py` — add/remove env var | `.env.example` (add entry with comment), `README.md` (Variables table), `tests/conftest.py` (`mock_settings` fixture) |
| `app/routers/<name>.py` — add endpoint | `app/routers/__init__.py` (export), `app/main.py` (include_router), `README.md` (API Endpoints table), `tests/test_routers.py` |
| `app/utils/<name>.py` — new utility | `app/utils/__init__.py` (export), `tests/test_<name>.py` (new test file) |
| `requirements.txt` — add dependency | `Dockerfile` (verify it installs; no change needed for pip install -r) |
| `app/utils/language_code.py` — add language | Verify the locale is supported by Azure Speech Services |
| `app/utils/subtitle_utils.py` — change SRT format | `tests/test_subtitle_utils.py` (update expected output) |
| `app/utils/skip_checker.py` — add skip condition | `app/config.py` (`SkipConfig` field + env var), `.env.example`, `README.md` (Variables table) |
| `.github/workflows/tests.yml` — change test command | `AGENTS.md` (Build & Run section) |
| `Dockerfile` — change base image or Python version | `tests.yml` (`python-version` matrix), `AGENTS.md` (Tech Stack section) |
| `app/transcription_service.py` — change session/job tracking | `app/routers/batch.py`, `app/routers/asr.py`, `app/routers/webhooks.py` (all consume TranscriptionService) |
