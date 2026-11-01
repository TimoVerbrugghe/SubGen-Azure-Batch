# SubGen-Azure-Batch

[![Donate to original subgen project](https://img.shields.io/badge/Donate_to_original_SubGen_project-PayPal-green.svg)](https://www.paypal.com/donate/?hosted_button_id=SU4QQP6LH5PF6)
[![AI Ready](https://img.shields.io/badge/AI--Ready-yes-brightgreen?style=flat)](https://github.com/johnpapa/ai-ready)

<img src="https://raw.githubusercontent.com/TimoVerbrugghe/subgen-azure-batch/main/icon.png" alt="SubGen-Azure-Batch Logo" width="200" />

## What is this?

SubGen-Azure-Batch is a fork of [SubGen](https://github.com/McCloudS/subgen) that replaces local Whisper processing with Azure's cloud-based Batch Transcription API.

This will transcribe your personal media on a Plex, Emby, or Jellyfin server to create subtitles (.srt) from audio/video files. It can also be used as a Whisper provider in Bazarr. It is reliant on webhooks from Jellyfin, Emby, Plex, or Tautulli.

Key benefits of SubGen-Azure-Batch:

- ✅ **No GPU required** - Lightweight container, no CUDA dependencies
- ✅ **Scalable** - Azure handles the heavy lifting
- ✅ **Cost-effective** - Pay only for what you use
- ✅ **High quality** - Same Whisper models, cloud-hosted
- ✅ **Web UI** - Browse files and submit batch jobs visually

## Changes vs the original SubGen

| Feature | Original SubGen | SubGen-Azure-Batch |
| ------- | --------------- | ------------------ |
| **Transcription Engine** | Local Whisper (faster-whisper + stable-ts) | Azure Batch Transcription API |
| **GPU Required** | Yes (for reasonable speed) | No |
| **Docker Image Size** | ~8GB (CUDA) | ~500MB |
| **Processing Style** | Real-time, synchronous | Batch (asynchronous) |
| **Cost Model** | Hardware investment | Pay-per-use (Azure) |
| **Web UI** | Removed in recent versions | Included - browse files and submit jobs |
| **Standalone Mode** | Yes (launcher.py) | Docker only |
| **Metadata Refresh** | After queue start | After transcription completion |
| **Bazarr Notifications** | Not available | Notifies Bazarr when subtitles are generated via webhooks/UI |
| **Pushover Notifications** | Not available | Per-file failure alerts and batch completion summaries |

**Removed Environment Variables** (not applicable to SubGen-Azure-Batch):

- `TRANSCRIBE_DEVICE` - No local GPU/CPU selection needed
- `WHISPER_MODEL` - Azure uses its own models
- `WHISPER_THREADS` - No local threading
- `MODEL_PATH` - No local model storage
- `COMPUTE_TYPE` - No local quantization
- `CLEAR_VRAM_ON_COMPLETE` - No local VRAM management
- `MONITOR` - Folder monitoring not implemented
- `TRANSCRIBE_FOLDERS` - Use the Web UI batch feature instead
- `HF_TRANSFORMERS` / `HF_BATCH_SIZE` - No Hugging Face support
- `USE_MODEL_PROMPT` / `CUSTOM_MODEL_PROMPT` - Azure handles prompting
- `CUSTOM_REGROUP` - Azure handles subtitle grouping
- `WORD_LEVEL_HIGHLIGHT` - Not supported by Azure API
- `SUBGEN_KWARGS` - No local Whisper kwargs
- `TRANSCRIBE_OR_TRANSLATE` - Azure only transcribes (no translation to English)
- `PLEX_QUEUE_NEXT_EPISODE` / `PLEX_QUEUE_SEASON` / `PLEX_QUEUE_SERIES` - Not implemented
- `SHOW_IN_SUBNAME_MODEL` - No model name in filename (Azure doesn't expose model name)
- OpenAI API configuration - Uses Azure instead

**New features in SubGen-Azure-Batch:**

- Web UI for browsing media and submitting batch transcription jobs
- Azure Speech Services integration with blob storage
- Pushover notifications for per-file failures and batch completion summaries
- Bazarr integration - automatically notifies Bazarr when subtitles are generated
- Authenticated REST and MCP interfaces for agent-submitted transcription batches

## How do I set it up?

## Prerequisites

You'll need:

- **Azure Speech Services** - [Create a resource](https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices)
- **Azure Storage Account** - [Create a storage account](https://portal.azure.com/#create/Microsoft.StorageAccount-ARM)
- **Pushover (optional)** - Create a [Pushover account](https://pushover.net/) and [application](https://pushover.net/apps/build) for notifications. Configure the application API token and your user key with `PUSHOVER_API_TOKEN` and `PUSHOVER_USER_KEY`.

## Docker

The dockerfile is in the repo along with an example docker-compose file.

You MUST mount your media volumes in SubGen-Azure-Batch the same way Plex (or your media server) sees them. For example, if Plex uses `/Share/media/TV:/tv` you must have that identical volume in SubGen-Azure-Batch. If that is not possible, please use the PATH MAPPING environment variables (see variabled below).

### Access the Web UI

Open [http://localhost:9000](http://localhost:9000) to:

- Browse your media files
- Submit batch transcription jobs
- Monitor transcription progress

## Bazarr

You only need to configure the Whisper Provider as shown below:

![Bazarr Whisper configuration](https://wiki.bazarr.media/Additional-Configuration/images/whisper_config.png)
The Docker Endpoint is the IP address and port of your SubGen-Azure-Batch container (e.g., http://192.168.1.111:9000). See https://wiki.bazarr.media/Additional-Configuration/Whisper-Provider/ for more info. **127.0.0.1 WILL NOT WORK IF YOU ARE RUNNING BAZARR IN A DOCKER CONTAINER!** 

I recommend not enabling the Bazarr provider with other webhooks in SubGen-Azure-Batch, or you will likely generate duplicate subtitles. If you are using Bazarr, path mapping isn't necessary, as Bazarr sends the file over HTTP.

**Note:** SubGen-Azure-Batch can also notify Bazarr when subtitles are generated via the Web UI or media server webhooks. Configure `BAZARR_URL` and `BAZARR_API_KEY` to enable this feature.

Web UI batches notify Bazarr after all jobs finish, with one scan attempt per unique series or movie. The batch completion notification is sent before waiting for Bazarr, so a slow scan cannot delay it. HTTP rejections and uncertain transport outcomes are logged separately; after a timeout, Bazarr may still be scanning. Unconfirmed scans are excluded from the successful notification count and never change completed transcription jobs to failed. A full scan is only requested when no targeted scans can be attempted, not as a retry for a failed or unconfirmed targeted scan. Scan requests have a 300-second total timeout and 30-second connection timeout.

## Plex

Create a webhook in Plex that will call back to your SubGen-Azure-Batch address, e.g., http://192.168.1.111:9000/plex. See: https://support.plex.tv/articles/115002267687-webhooks/

You will also need to generate a token. Remember, Plex and SubGen-Azure-Batch need to be able to see the exact same files at the exact same paths, otherwise you need `USE_PATH_MAPPING`.

## Emby

All you need to do is create a webhook in Emby pointing to your SubGen-Azure-Batch, e.g., `http://192.168.1.154:9000/emby`, set `Request content type` to `multipart/form-data` and configure your desired events (usually `New Media Added`, `Start`, and `Unpause`). See https://github.com/McCloudS/subgen/discussions/115#discussioncomment-10569277 for screenshot examples.

Emby provides good information in their responses, so we don't need to add an API token or server URL to query for more information.

Remember, Emby and SubGen-Azure-Batch need to be able to see the exact same files at the exact same paths, otherwise you need `USE_PATH_MAPPING`.

## Tautulli

Create the webhooks in Tautulli with the following settings:

- **Webhook URL:** http://yourdockerip:9000/tautulli
- **Webhook Method:** POST
- **Triggers:** Whatever you want, but you'll likely want "Playback Start" and "Recently Added"

**Playback Start - Header:**

```json
{ "source":"Tautulli" }
```

**Playback Start - Data:**

```json
{
    "event":"played",
    "file":"{file}",
    "filename":"{filename}",
    "mediatype":"{media_type}"
}
```

**Recently Added - Header:**

```json
{ "source":"Tautulli" }
```

**Recently Added - Data:**

```json
{
    "event":"added",
    "file":"{file}",
    "filename":"{filename}",
    "mediatype":"{media_type}"
}
```

## Jellyfin

First, you need to install the Jellyfin webhooks plugin. Then click "Add Generic Destination", name it anything you want, webhook URL is your SubGen-Azure-Batch info (e.g., http://192.168.1.154:9000/jellyfin). Next, check Item Added, Playback Start, and Send All Properties. Last, "Add Request Header" and add the Key: `Content-Type` Value: `application/json`

Click Save and you should be all set!

Remember, Jellyfin and SubGen-Azure-Batch need to be able to see the exact same files at the exact same paths, otherwise you need `USE_PATH_MAPPING`.

## Variables

You can define the port via environment variables, but the endpoints are static.

The following environment variables are available in Docker. They will default to the values listed below.

| Variable | Default Value | Description |
| -------- | ------------- | ----------- |
| **Azure Configuration (Required)** |   |   |
| AZURE_SPEECH_KEY | '' | **(New)** Azure Speech Services API key |
| AZURE_SPEECH_REGION | 'swedencentral' | **(New)** Azure region for Speech Services |
| AZURE_STORAGE_CONNECTION_STRING | '' | **(New)** Azure Blob Storage connection string |
| AZURE_STORAGE_CONTAINER | 'transcription-audio' | **(New)** Container name for temporary audio uploads |
| **Server Settings** |   |   |
| DEBUG | False | Provides debug data that can be helpful to troubleshoot issues |
| UVICORN_TIMEOUT_KEEP_ALIVE | (unset) | **(New)** TCP keepalive timeout in seconds. Set to prevent connection resets during long transcriptions (e.g., 300). Only applied if set. |
| **Media Settings** |   |   |
| MEDIA_FOLDERS | '/tv,/movies' | **(New)** Comma-separated list of paths to show in the Web UI file browser |
| SUBTITLE_LANGUAGE | '' | Default subtitle language code (leave empty for auto-detect) |
| **Agent API** |   |   |
| AGENT_API_KEY | '' | **(New)** Bearer key for agent REST/MCP access. Empty disables these interfaces; configure a unique key and keep the service on a trusted network. |
| AGENT_MCP_ALLOWED_HOSTS | '' | **(New)** Optional comma-separated MCP Host header allowlist. Empty/unset accepts any host and Origin; a non-empty list enables DNS-rebinding protection. API-key authentication is always required. |
| **Processing Settings** |   |   |
| CONCURRENT_TRANSCRIPTIONS | 10 | Maximum number of files processed end-to-end concurrently — covers audio extraction, blob upload, and Azure transcription. Raise to increase throughput; lower to reduce CPU pressure from simultaneous FFmpeg processes. Azure's per-subscription quota is enforced by an internal guard. Bazarr requests are always served before batch jobs in the queue. |
| TRANSCODE_DIR | '/transcode' | Directory for temp audio files. Mount a volume here to reduce memory usage during batch processing |
| JOB_POLL_INTERVAL | 30 | Seconds between polling Azure for job status. Azure recommends no more than once per minute. |
| TRANSCRIPTION_TIMEOUT | 3600 | Maximum seconds to wait for a transcription job before giving up. Combined with JOB_POLL_INTERVAL to determine how many polls are attempted. |
| MAX_CONCURRENT_UPLOADS | 2 | Maximum parallel blob uploads. Keep low to avoid Azure storage write-timeout errors under throttling. |
| PROCESS_ADDED_MEDIA | False | Process media when added to library (requires webhook integration) |
| PROCESS_MEDIA_ON_PLAY | False | Process media when played (requires webhook integration) |
| **Path Mapping** |   |   |
| USE_PATH_MAPPING | False | Enable path translation between media server and container paths |
| PATH_MAPPING_FROM | '' | This is the path of media relative to your media server |
| PATH_MAPPING_TO | '' | This is the path of that same folder relative to SubGen-Azure-Batch |
| **Skip Conditions** |   |   |
| SKIP_IF_TARGET_SUBTITLES_EXIST | True | Skip generation if a subtitle matching the target language already exists |
| SKIP_IF_EXTERNAL_SUBTITLES_EXIST | False | Skip generation if any external subtitle file exists |
| SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE | '' | Skip if internal subtitles exist in this language (e.g., 'eng') |
| SKIP_ONLY_SUBGEN_SUBTITLES | False | Only skip if existing subtitle was created by SubGen (has '.subgen.' in filename) |
| SKIP_IF_AUDIO_TRACK_IS | '' | Skip if audio track is in these languages (pipe-separated, e.g., 'en\|eng') |
| SKIP_SUBTITLE_LANGUAGES | '' | Skip if any subtitle exists in these languages (pipe-separated) |
| SKIP_UNKNOWN_LANGUAGE | False | Skip if the audio track has an unknown/undefined language |
| SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST | False | Skip if no audio language is set but subtitles already exist |
| **Transcription Options** |   |   |
| FORCE_DETECTED_LANGUAGE_TO | '' | Force all transcriptions to use this language code (e.g., 'en', 'es') |
| APPEND | False | Append "Transcribed by SubGen-Azure-Batch" credit line at end of subtitles |
| LRC_FOR_AUDIO_FILES | True | Write .lrc files instead of .srt for audio files (.mp3, .flac, etc.) |
| PREFERRED_AUDIO_LANGUAGES | '' | Preferred audio track languages for extraction (pipe-separated, e.g., 'eng\|deu') |
| LIMIT_TO_PREFERRED_AUDIO_LANGUAGE | False | Skip files without audio in preferred languages |
| DETECT_LANGUAGE_LENGTH | 30 | Detect language on the first X seconds of audio |
| DETECT_LANGUAGE_OFFSET | 0 | Start language detection X seconds into the file (skip intros) |
| LANGUAGE_DETECTION_CANDIDATES | 'en-US,nl-NL,es-ES,fr-FR' | **(New)** Candidate locales for Azure language identification (max 4, comma-separated). Used by Bazarr's `/detect-language` endpoint. See [supported locales](https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=stt). |
| **Subtitle Naming** |   |   |
| SUBTITLE_LANGUAGE_NAMING_TYPE | 'ISO_639_2_B' | Language format: ISO_639_1, ISO_639_2_T, ISO_639_2_B, NAME, NATIVE |
| SUBTITLE_LANGUAGE_NAME | '' | Override detected language in filename (e.g., 'aa' to sort higher in Plex) |
| SHOW_IN_SUBNAME_SUBGEN | False | Include '.subgen' marker in subtitle filename |
| **Media Server Integration** |   |   |
| PLEX_SERVER | '' | Your local Plex server address (e.g., http://plex:32400) |
| PLEX_TOKEN | '' | Your Plex token (see: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |
| JELLYFIN_SERVER | '' | Your Jellyfin server address (e.g., http://jellyfin:8096) |
| JELLYFIN_TOKEN | '' | Generate a token inside the Jellyfin interface |
| EMBY_SERVER | '' | Your Emby server address |
| EMBY_TOKEN | '' | Your Emby API token |
| **Bazarr Integration** |   |   |
| BAZARR_URL | '' | **(New)** Bazarr server URL for notifications (e.g., http://bazarr:6767) |
| BAZARR_API_KEY | '' | **(New)** Bazarr API key for notifications |
| **Notifications** |   |   |
| PUSHOVER_USER_KEY | '' | **(New)** Pushover user/group key. Required with `PUSHOVER_API_TOKEN` to send notifications. |
| PUSHOVER_API_TOKEN | '' | **(New)** Pushover application API token. Create an application in Pushover to obtain it. |
| NOTIFY_ON_FAILURE | True | **(New)** Send per-file alerts and a batch summary when one or more files fail. |
| NOTIFY_ON_SUCCESS | False | Send a batch summary when all processed files succeed. Disabled by default. |

Batch summaries include total, completed, failed, and skipped file counts. Both Pushover credentials are required. Successful batch summaries are opt-in; set `NOTIFY_ON_SUCCESS=true` to enable them. Failure summaries follow `NOTIFY_ON_FAILURE`, which also controls the existing per-file failure alerts.

Summaries also identify the batch ID, requested languages, and unique media files.
Episodes are grouped by show and season folder when recognizable; movie folders
or filenames provide a fallback. Up to six titles and two failed filenames are
shown, with remaining-title counts and a 1024-character message limit. File counts
are deduplicated across languages, while the completion counters count transcription
jobs. No metadata service, database, or additional configuration is required.

## Queue Management and Concurrency

SubGen-Azure-Batch uses a priority-aware concurrency system to ensure Bazarr subtitle requests are never blocked behind large batch jobs.

### Pipeline Semaphore

`CONCURRENT_TRANSCRIPTIONS` governs end-to-end pipeline concurrency: a single slot covers audio extraction (FFmpeg), blob upload, Azure transcription, and cleanup. All sources — Web UI batches, Bazarr ASR, webhooks — share this single pool of slots.

When all slots are occupied, incoming requests wait in a **priority queue**:

| Source | Priority | Behaviour |
|--------|----------|-----------|
| Bazarr `/asr` | **High (0)** | Served next, ahead of any waiting batch jobs |
| Web UI / webhooks | Normal (1) | Served in FIFO order among themselves |

This means a Bazarr request submitted while a 500-file batch is running will be processed as soon as the next slot is free, without needing a separate worker pool.

### Internal Azure Quota Guard

Azure Speech Services imposes a per-subscription limit on simultaneous batch transcription jobs (200 for the S0 tier). An internal semaphore — not user-configurable — enforces this limit around the `create_transcription` → `delete_transcription` window, independent of `CONCURRENT_TRANSCRIPTIONS`. This prevents 429 throttling even if `CONCURRENT_TRANSCRIPTIONS` is set very high.

### Upload Semaphore

`MAX_CONCURRENT_UPLOADS` (default 2) limits simultaneous blob uploads independently to avoid Azure Storage write-timeout errors when many jobs reach the upload phase at the same time.

## Agent API and MCP Server

Agents can submit media files or folders for batch transcription through the authenticated REST API or built-in Model Context Protocol (MCP) server. Set a unique `AGENT_API_KEY` of at least 32 non-whitespace characters to enable either interface. If the configured key is missing or too short, requests fail closed with HTTP 503; once enabled, a missing or incorrect `X-API-Key` header receives HTTP 401.

Agent paths must be absolute paths visible inside the service container and located under a configured `MEDIA_FOLDERS` root. The service resolves symlinks before access, rejects path traversal and paths outside those roots, and only accepts supported media files. For example, mount `/mnt/tv` as `/tv` and request `/tv/Series/Season 1`; do not pass a host path that the container cannot see.

The `languages` list accepts ISO 639 codes (for example `en`, `de`) or Azure locales (for example `en-US`, `de-DE`). Each file is queued once per requested language, with a maximum of four languages and 1000 file-language jobs per request. This is explicit-language transcription, not Azure language identification. `apply_skip_rules` defaults to `true`; `notify_bazarr` defaults to `false` for agent requests. These options are attached to the individual batch and do not change Web UI or webhook defaults.

Agent configuration is environment-only; the API key and MCP host allowlist are not written to a database or UI settings. Batch sessions use the application's existing in-memory tracking. After a process or container restart, tracked batch IDs and statuses are lost, and unfinished batches are not resumed. A restart also does not guarantee cleanup or cancellation of Azure resources already created for those jobs.

### REST examples

```bash
curl -X POST http://localhost:9000/api/agent/batches \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "folders": ["/tv/Example Series"],
    "files": ["/movies/Example Movie.mkv"],
    "languages": ["en-US", "fr"],
    "apply_skip_rules": true,
    "notify_bazarr": false
  }'
```

The response contains a `session_id`. List active agent batches with `GET /api/agent/batches`, retrieve aggregate and per-file stage details with `GET /api/agent/batches/{session_id}`, and cancel with `POST /api/agent/batches/{session_id}/cancel`. Per-file stages include `queued`, `extracting`, `uploading`, `transcribing`, `completed`, `failed`, and `cancelled`. Submission and cancellation require Azure configuration; list/status requests do not start Azure work.

### MCP client setup

The MCP server is hosted **inside SubGen**, alongside the web UI on port 9000.
No separate MCP process or local bridge is required in Docker or Kubernetes.
It uses Streamable HTTP at `http://<host>:9000/mcp`, or
`https://<host>/mcp` behind an HTTPS reverse proxy.
Configure your client to send `X-API-Key` on every request.

`AGENT_MCP_ALLOWED_HOSTS` is optional: empty or unset accepts any Host and Origin,
but **does not bypass API-key authentication**. This disables the SDK's
DNS-rebinding host/origin checks. For defense in depth, set a comma-separated
allowlist such as `subgen.example.com,subgen.local:9000,localhost:*`.
Use the externally requested hostname (include the port when present); corresponding
HTTP/HTTPS origins are allowed automatically. This setting is not an IP firewall
or a list of client machines.

For clients using a JSON server configuration, the shape is:

```json
{
  "mcpServers": {
    "subgen": {
      "url": "https://subgen.example.com/mcp",
      "headers": {
        "X-API-Key": "<your-AGENT_API_KEY>"
      }
    }
  }
}
```

The MCP tools are `submit_transcription_batch`, `list_active_batches`, `get_batch_status`, and `cancel_batch`. The submit tool accepts the same file/folder, language, skip-rule, and Bazarr options as REST.

### Docker deployment

The included [docker-compose.yml](docker-compose.yml) passes both agent variables
and Pushover settings from `.env` into the container. Generate a unique API key
locally (`openssl rand -hex 32`), place it in `.env` as `AGENT_API_KEY`, and never
commit that file. Optionally set `AGENT_MCP_ALLOWED_HOSTS`; leave it blank to accept
any hostname. Then run:

```bash
docker compose up -d --build
```

Connect on a trusted LAN at `http://<docker-host>:9000/mcp`, or configure an HTTPS
reverse proxy for `https://subgen.example.com/mcp`. The same API-key requirement
applies in either case.

### Kubernetes deployment

The [example manifest](examples/kubernetes.yaml) contains a single-replica
Deployment, ClusterIP Service, and HTTPS Ingress for the hosted MCP server.
Before applying it:

1. Select your namespace and a published image tag/digest containing these features.
2. Replace the media PVC names with your existing writable RWX media claims.
3. Provision a `subgen-secrets` Secret in that namespace with `AZURE_SPEECH_KEY`,
   `AZURE_STORAGE_CONNECTION_STRING`, and `AGENT_API_KEY`. Optionally add
   `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN`. Use your secret manager; do not
   commit live credentials. Set `AZURE_SPEECH_REGION` to your resource's region.
4. Replace the Ingress hostname, ingress class, and TLS Secret name. Provision a
   matching TLS certificate, and optionally set `AGENT_MCP_ALLOWED_HOSTS`.
5. Apply: `kubectl apply -n <namespace> -f examples/kubernetes.yaml`.

Ingress must route `/mcp` (including `/mcp/`) to port 9000 and preserve
`X-API-Key`, `Mcp-Session-Id`, `MCP-Protocol-Version`, and streaming responses.
Disable response buffering where your ingress requires it and allow long-lived
streaming requests. These settings are ingress-controller-specific.
Use one replica: batch and MCP session tracking is in memory; multiple replicas
would require session affinity and do not share batch state.
The example mounts `/transcode` as an `emptyDir` for temporary audio; no settings
database or additional persistent application state is introduced.

Only the agent REST API and `/mcp` are API-key protected. The existing web UI and
legacy routes retain their behavior; keep the entire service on a trusted private
network or add authentication at the ingress for those routes.

**Network security:** Treat the API key as a bearer secret. Keep this service on localhost or a trusted private network, restrict access with firewall rules, and use a TLS-terminating reverse proxy if clients connect across an untrusted network. Do not expose the service or MCP endpoint directly to the public internet.

## API Endpoints

| Endpoint | Description |
| -------- | ----------- |
| `GET /` | Web UI |
| `GET /health` | Health check |
| `GET /status` | Returns SubGen-Azure-Batch version |
| `POST /asr` | Bazarr-compatible ASR endpoint (returns SRT) |
| `GET /detect-language` | Language detection endpoint (Bazarr compatibility) |
| `POST /detect-language` | Language detection with audio upload (uses Azure language identification) |
| `POST /plex` | Plex webhook |
| `POST /jellyfin` | Jellyfin webhook |
| `POST /emby` | Emby webhook |
| `POST /tautulli` | Tautulli webhook |
| `POST /api/batch/submit` | Submit batch transcription via API |
| `GET /api/batch/sessions/{id}` | Get session status |
| `POST /api/agent/batches` | Submit a secured agent batch (requires `X-API-Key`) |
| `GET /api/agent/batches` | List active secured agent batches |
| `GET /api/agent/batches/{id}` | Get secured batch counts and per-file stages |
| `POST /api/agent/batches/{id}/cancel` | Cancel a secured agent batch |
| `POST /mcp` | MCP Streamable HTTP server (requires `X-API-Key`) |
| `GET /docs` | OpenAPI/Swagger documentation |

## Supported Languages

SubGen-Azure-Batch supports 99+ languages via Azure Speech Services. See the [full list](https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=stt).

Common languages:

- English (en), Spanish (es), French (fr), German (de)
- Japanese (ja), Chinese (zh), Korean (ko)
- Portuguese (pt), Italian (it), Russian (ru)
- And many more...

## Contributing

Contributions are welcome! To get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Run the tests: `pytest tests/ -v --tb=short -m "not azure_api and not integration and not slow"`
5. Run the linter: `ruff check app/ tests/ && ruff format --check app/ tests/`
6. Open a pull request targeting `main`

See `AGENTS.md` for architecture conventions and `tests/conftest.py` for test fixture documentation.

> **Note:** Azure API tests (`@pytest.mark.azure_api`) require real Azure credentials and are excluded from CI. Copy `tests/.env.example` to `tests/.env` to run them locally.

## License

MIT License - see [LICENSE](LICENSE)

## Credits

SubGen-Azure-Batch is built on top of the original [SubGen](https://github.com/McCloudS/subgen) project by [McCloudS](https://github.com/McCloudS). 

The original SubGen project provides local Whisper-based subtitle generation and inspired the architecture and webhook integrations used in SubGen-Azure-Batch. We are grateful for McCloudS's work in creating and maintaining the original project.

**Consider supporting the original project:**

[![Donate to original subgen project](https://img.shields.io/badge/Donate_to_original_SubGen_project-PayPal-green.svg)](https://www.paypal.com/donate/?hosted_button_id=SU4QQP6LH5PF6)

**Additional credits:**

- [Azure Speech Services](https://azure.microsoft.com/services/cognitive-services/speech-services/) - Cloud transcription API
- [FFmpeg](https://ffmpeg.org/) - Audio extraction
- [Whisper ASR Webservice](https://github.com/ahmetoner/whisper-asr-webservice) - Bazarr webhook implementation reference
