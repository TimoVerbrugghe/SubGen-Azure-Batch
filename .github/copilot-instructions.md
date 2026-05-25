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
