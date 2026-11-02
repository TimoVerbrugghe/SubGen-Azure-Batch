# Copilot Instructions — SubGen-Azure-Batch

## Documentation

- Keep `README.md` user-facing: overview, setup, configuration, usage, and concise practical troubleshooting.
- Put technical implementation details, protocol internals, diagnostic reasoning, and maintainer notes in `.github/copilot-instructions.md`, not in the README.
- When code changes affect users, update the README with what they need to do or expect; keep the underlying technical explanation here rather than duplicating it.

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

## Bazarr Scan Coordination and Batch Notifications

- Keep Pushover batch completion independent of Bazarr. In `app/routers/batch.py`, attempt the completion summary once after batch jobs finish and before any Bazarr status check or scan. Respect Pushover credentials, `NOTIFY_ON_SUCCESS`, and `NOTIFY_ON_FAILURE`; cancelled batches do not send completion summaries. Bazarr failures must neither suppress Pushover nor fail completed transcriptions.
- Route all batch and webhook scans through `BazarrClient` in `app/utils/bazarr_client.py`. Targeted `PATCH /api/series` and `PATCH /api/movies` with `action=scan-disk` execute synchronously. Hold the shared per-server, per-event-loop lock until the response completes; client-instance locks do not protect concurrent batches/webhooks.
- Deduplicate targeted scans per unique series/movie within a batch, but retain subsequent scans after later subtitle writes. An earlier scan cannot discover subtitles written afterwards.
- Before a scan, read authenticated `GET /api/system/tasks` using the `X-API-KEY` header. Wait up to 300 seconds for any `job_running=true` entry to finish, polling every two seconds with status requests bounded to 30 seconds or the remaining deadline. Unavailable/malformed status or a busy deadline means `NOT_ATTEMPTED` with diagnostics, not successful notification.
- Bazarr's task-status endpoint tracks scheduled jobs, not targeted API scans. Idle scheduler status cannot prove that a timed-out targeted scan has finished. Keep the server's scan state uncertain after a timeout, connection loss, or in-flight cancellation, and pause subsequent scans until SubGen restarts. Log recovery guidance: verify Bazarr is idle or recover Bazarr before restarting SubGen. A failed read-only preflight must not permanently pause scans.
- Preserve `CONFIRMED`, `REJECTED`, `UNCONFIRMED`, and `NOT_ATTEMPTED` outcomes. Only confirmed scans count as successful; never retry an uncertain scan. Targeted scan requests use a 300-second total timeout and 30-second connection timeout.
- Never fall back to asynchronous full-library jobs (`update_series` / `update_movies` via `POST /api/system/tasks`) for unmatched paths or unsuccessful scans. Log skipped unmatched paths and suggest a manual Bazarr scan instead.
- Coordination is local to one SubGen process/event loop and server URL. Manual scans, other integrations, multiple processes, and scheduled jobs starting after preflight can still overlap; do not claim this guarantees freedom from SQLite locks.
- Preserve public-interface tests in `tests/test_bazarr_client.py` for cross-client serialization, later writes, busy/unusable status, uncertain outcomes, cancellation, and server isolation. Keep `tests/test_routers.py` coverage proving completion notification precedes scans and unmatched paths never launch full-library jobs.

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

## Bazarr ASR Transport and ConnectionError Diagnostics

### Provider status and request flow

- Bazarr's [Whisper provider](https://github.com/morpheus65535/bazarr/blob/v1.5.6/custom_libs/subliminal_patch/providers/whisperai.py) uploads raw 16-bit, 16 kHz mono PCM as multipart `audio_file` with `encode=false` to `POST /detect-language` or `POST /asr`, then consumes the complete JSON or SRT response.
- **System > Providers** displays stored provider-throttling state, not a connectivity heartbeat. Refresh reloads that state; Reset clears errors/backoff; Good does not prove a successful request. Backoff duration depends on the Bazarr version, so inspect the deployed version and logs rather than assuming five minutes.
- Manual search can call language detection when audio-language metadata is missing or ambiguous; otherwise it can return a candidate without contacting SubGen. Downloading a `whisperai` candidate triggers the real ASR request.
- Bazarr 1.5.6 consumes `response.content` without checking the HTTP status before logging completion. A rejected upload can therefore appear as Completed/Good and subsequently fail subtitle validation. Verify the ASR response, saved SRT, and Bazarr job completion, not just the provider status.
- Distinguish inbound Bazarr ASR from SubGen's outbound scan notifications: `BAZARR_URL` only configures the latter.

### Uploads, temporary storage, and timeouts

- A `ConnectionError` identifies a transport failure, not a specific Azure error. Inspect the full Bazarr exception and correlate both applications' logs to distinguish DNS/connect failures, upload stalls, idle responses, and pod restarts/OOM kills.
- Bazarr's [HTTP client](https://github.com/morpheus65535/bazarr/blob/v1.5.6/libs/urllib3/connectionpool.py) applies the connection timeout while sending multipart uploads, then switches to the read timeout. The five-second default can cause upload failures before SubGen enters the route handler; recommend 60 seconds for large uploads or slower networks.
- An hour of raw PCM is approximately 110 MiB. Allow large multipart uploads through proxies; response heartbeats cannot protect an upload that has not finished.
- Starlette spills large uploads to Python's temporary directory before calling the route handler (default spool threshold: 1 MiB). With `readOnlyRootFilesystem`, mount a writable temporary volume and set `TMPDIR` to it, for example `/transcode`. `TRANSCODE_DIR` alone does not configure Python's temporary directory.
- No writable temporary directory causes `FileNotFoundError: No usable temporary directory found`, wrapped by FastAPI as HTTP 400 with `There was an error parsing the body`. Reproduce safely with an upload larger than 1 MiB and an invalid `task`: successful parsing should reach query validation and return HTTP 422 without creating an Azure job.
- Bazarr's transcription/translation timeout (normally 3600 seconds) is a socket read-idle timeout, not a total-duration limit. SubGen's `TRANSCRIPTION_TIMEOUT` sets a processing deadline including queueing, extraction, and Azure calls. Cancellation performs cleanup, so request termination may take longer than the deadline.
- `UVICORN_TIMEOUT_KEEP_ALIVE` only controls idle HTTP connections **between** requests; it does not protect active uploads or transcriptions.
- Verify reachability from the Bazarr host/container. Inside Kubernetes, use `http://subgen.<namespace>.svc.cluster.local:9000`; outside it, use a reachable LoadBalancer or ingress hostname. ClusterIP addresses and cluster DNS usually are not reachable externally.

For basic connectivity checks, substitute Bazarr's configured SubGen base URL:

```bash
curl --connect-timeout 10 --max-time 15 http://<subgen-host>:9000/health
curl --connect-timeout 10 --max-time 15 http://<subgen-host>:9000/status
```

### Response transport invariants

- SRT ASR and JSON language detection send an initial blank line and repeat it every `ASR_KEEPALIVE_INTERVAL` seconds (default 15). Whitespace is valid in both formats and prevents response-idle disconnects while Azure queues or processes work.
- TXT/VTT and other ASR formats stay buffered. Set the interval to 0 for clients requiring an exact buffered body and `Content-Length`; forced language detection returns immediately without Azure.
- Disable response buffering for both `/asr` and `/detect-language`, not just `/mcp`. SubGen sends `X-Accel-Buffering: no`, but only proxies honoring that header act on it.
- For ingress-nginx, the following annotations disable buffering and extend proxy timeouts:

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

- These annotations do not configure Traefik, Tailscale, or other controllers; use the controller's corresponding settings. Prefer direct Service access where possible to bypass HTTP proxy timeouts.
- Heartbeats cannot fix connection refusals, DNS failures, stalled uploads, or pod restarts/OOM kills.
- Log real processing failures; never substitute successful-looking subtitles or an undetermined language. After streaming headers are sent, failure/timeout must abort the stream rather than complete it. With heartbeats disabled, known processing errors return HTTP 500/504 JSON.
- Preserve stream failure and disconnect propagation through middleware. MCP authentication must remain pass-through ASGI middleware rather than wrapping the response in function-style HTTP middleware.
- Language detection uses the shared Bazarr-priority processing pool and job tracking. Require one to four candidate locales; unsupported forced languages or invalid candidate counts return HTTP 503. Query offsets must be non-negative and sample lengths positive.
