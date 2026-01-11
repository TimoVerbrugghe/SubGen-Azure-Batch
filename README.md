[![Donate to original subgen project](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/donate/?hosted_button_id=SU4QQP6LH5PF6)

<img src="https://raw.githubusercontent.com/TimoVerbrugghe/subgen-azure-batch/main/icon.png" width="200">

# What is this?

SubGen-Azure-Batch is a fork of [SubGen](https://github.com/McCloudS/subgen) that replaces local Whisper processing with Azure's cloud-based Batch Transcription API.

This will transcribe your personal media on a Plex, Emby, or Jellyfin server to create subtitles (.srt) from audio/video files. It can also be used as a Whisper provider in Bazarr. It is reliant on webhooks from Jellyfin, Emby, Plex, or Tautulli.

Key benefits of SubGen-Azure-Batch:
- ✅ **No GPU required** - Lightweight container, no CUDA dependencies
- ✅ **Scalable** - Azure handles the heavy lifting
- ✅ **Cost-effective** - Pay only for what you use
- ✅ **High quality** - Same Whisper models, cloud-hosted
- ✅ **Web UI** - Browse files and submit batch jobs visually

# Changes vs the original SubGen

| Feature | Original SubGen | SubGen-Azure-Batch |
|---------|----------------|--------------|
| **Transcription Engine** | Local Whisper (faster-whisper + stable-ts) | Azure Batch Transcription API |
| **GPU Required** | Yes (for reasonable speed) | No |
| **Docker Image Size** | ~8GB (CUDA) | ~500MB |
| **Processing Style** | Real-time, synchronous | Batch (asynchronous) |
| **Cost Model** | Hardware investment | Pay-per-use (Azure) |
| **Web UI** | Removed in recent versions | Included - browse files and submit jobs |
| **Standalone Mode** | Yes (launcher.py) | Docker only |
| **Metadata Refresh** | After queue start | After transcription completion |
| **Bazarr Notifications** | Not available | Notifies Bazarr when subtitles are generated via webhooks/UI |
| **Pushover Notifications** | Not available | Notifies on transcription failures |

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
- Pushover notifications for transcription failures
- Bazarr integration - automatically notifies Bazarr when subtitles are generated

# How do I set it up?

## Prerequisites

You'll need:
- **Azure Speech Services** - [Create a resource](https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices)
- **Azure Storage Account** - [Create a storage account](https://portal.azure.com/#create/Microsoft.StorageAccount-ARM)

## Docker

The dockerfile is in the repo along with an example docker-compose file.

You MUST mount your media volumes in SubGen-Azure-Batch the same way Plex (or your media server) sees them. For example, if Plex uses `/Share/media/TV:/tv` you must have that identical volume in SubGen-Azure-Batch. If that is not possible, please use the PATH MAPPING environment variables (see variabled below).

### Access the Web UI

Open [http://localhost:9000](http://localhost:9000) to:

- Browse your media files
- Submit batch transcription jobs
- Monitor transcription progress

## Bazarr

You only need to configure the Whisper Provider as shown below: <br>
![bazarr_configuration](https://wiki.bazarr.media/Additional-Configuration/images/whisper_config.png) <br>
The Docker Endpoint is the IP address and port of your SubGen-Azure-Batch container (e.g., http://192.168.1.111:9000). See https://wiki.bazarr.media/Additional-Configuration/Whisper-Provider/ for more info. **127.0.0.1 WILL NOT WORK IF YOU ARE RUNNING BAZARR IN A DOCKER CONTAINER!** 

I recommend not enabling the Bazarr provider with other webhooks in SubGen-Azure-Batch, or you will likely generate duplicate subtitles. If you are using Bazarr, path mapping isn't necessary, as Bazarr sends the file over HTTP.

**Note:** SubGen-Azure-Batch can also notify Bazarr when subtitles are generated via the Web UI or media server webhooks. Configure `BAZARR_URL` and `BAZARR_API_KEY` to enable this feature.

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

# Variables

You can define the port via environment variables, but the endpoints are static.

The following environment variables are available in Docker. They will default to the values listed below.

| Variable | Default Value | Description |
|----------|---------------|-------------|
| **Azure Configuration (Required)** |||
| AZURE_SPEECH_KEY | '' | **(New)** Azure Speech Services API key |
| AZURE_SPEECH_REGION | 'swedencentral' | **(New)** Azure region for Speech Services |
| AZURE_STORAGE_CONNECTION_STRING | '' | **(New)** Azure Blob Storage connection string |
| AZURE_STORAGE_CONTAINER | 'transcription-audio' | **(New)** Container name for temporary audio uploads |
| **Server Settings** |||
| DEBUG | False | Provides debug data that can be helpful to troubleshoot issues |
| **Media Settings** |||
| MEDIA_FOLDERS | '/tv,/movies' | **(New)** Comma-separated list of paths to show in the Web UI file browser |
| SUBTITLE_LANGUAGE | '' | Default subtitle language code (leave empty for auto-detect) |
| **Processing Settings** |||
| CONCURRENT_TRANSCRIPTIONS | 50 | **(Changed)** Number of jobs to process in parallel. Default increased for cloud processing |
| TRANSCODE_DIR | '/transcode' | **(New)** Directory for temp audio files. Mount a volume here to reduce memory usage during batch processing |
| JOB_POLL_INTERVAL | 10 | **(New)** Seconds between polling Azure for job status |
| PROCESS_ADDED_MEDIA | False | Process media when added to library (requires webhook integration) |
| PROCESS_MEDIA_ON_PLAY | False | Process media when played (requires webhook integration) |
| **Path Mapping** |||
| USE_PATH_MAPPING | False | Enable path translation between media server and container paths |
| PATH_MAPPING_FROM | '' | This is the path of media relative to your media server |
| PATH_MAPPING_TO | '' | This is the path of that same folder relative to SubGen-Azure-Batch |
| **Skip Conditions** |||
| SKIP_IF_TARGET_SUBTITLES_EXIST | True | Skip generation if a subtitle matching the target language already exists |
| SKIP_IF_EXTERNAL_SUBTITLES_EXIST | False | Skip generation if any external subtitle file exists |
| SKIP_IF_INTERNAL_SUBTITLES_LANGUAGE | '' | Skip if internal subtitles exist in this language (e.g., 'eng') |
| SKIP_ONLY_SUBGEN_SUBTITLES | False | Only skip if existing subtitle was created by SubGen (has '.subgen.' in filename) |
| SKIP_IF_AUDIO_TRACK_IS | '' | Skip if audio track is in these languages (pipe-separated, e.g., 'en\|eng') |
| SKIP_SUBTITLE_LANGUAGES | '' | Skip if any subtitle exists in these languages (pipe-separated) |
| SKIP_UNKNOWN_LANGUAGE | False | Skip if the audio track has an unknown/undefined language |
| SKIP_IF_NO_LANGUAGE_BUT_SUBTITLES_EXIST | False | Skip if no audio language is set but subtitles already exist |
| **Transcription Options** |||
| FORCE_DETECTED_LANGUAGE_TO | '' | Force all transcriptions to use this language code (e.g., 'en', 'es') |
| APPEND | False | Append "Transcribed by SubGen-Azure-Batch" credit line at end of subtitles |
| LRC_FOR_AUDIO_FILES | True | Write .lrc files instead of .srt for audio files (.mp3, .flac, etc.) |
| PREFERRED_AUDIO_LANGUAGES | '' | Preferred audio track languages for extraction (pipe-separated, e.g., 'eng\|deu') |
| LIMIT_TO_PREFERRED_AUDIO_LANGUAGE | False | Skip files without audio in preferred languages |
| DETECT_LANGUAGE_LENGTH | 30 | Detect language on the first X seconds of audio |
| DETECT_LANGUAGE_OFFSET | 0 | Start language detection X seconds into the file (skip intros) |
| **Subtitle Naming** |||
| SUBTITLE_LANGUAGE_NAMING_TYPE | 'ISO_639_2_B' | Language format: ISO_639_1, ISO_639_2_T, ISO_639_2_B, NAME, NATIVE |
| SUBTITLE_LANGUAGE_NAME | '' | Override detected language in filename (e.g., 'aa' to sort higher in Plex) |
| SHOW_IN_SUBNAME_SUBGEN | False | Include '.subgen' marker in subtitle filename |
| **Media Server Integration** |||
| PLEX_SERVER | '' | Your local Plex server address (e.g., http://plex:32400) |
| PLEX_TOKEN | '' | Your Plex token (see: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) |
| JELLYFIN_SERVER | '' | Your Jellyfin server address (e.g., http://jellyfin:8096) |
| JELLYFIN_TOKEN | '' | Generate a token inside the Jellyfin interface |
| EMBY_SERVER | '' | Your Emby server address |
| EMBY_TOKEN | '' | Your Emby API token |
| **Bazarr Integration** |||
| BAZARR_URL | '' | **(New)** Bazarr server URL for notifications (e.g., http://bazarr:6767) |
| BAZARR_API_KEY | '' | **(New)** Bazarr API key for notifications |
| **Notifications** |||
| PUSHOVER_USER_KEY | '' | **(New)** Pushover user key for failure notifications |
| PUSHOVER_API_TOKEN | '' | **(New)** Pushover application API token |
| NOTIFY_ON_FAILURE | True | **(New)** Send notification when a transcription job fails |

# API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Web UI |
| `GET /health` | Health check |
| `GET /status` | Returns SubGen-Azure-Batch version |
| `GET /asr` | Bazarr-compatible ASR endpoint |
| `POST /plex` | Plex webhook |
| `POST /jellyfin` | Jellyfin webhook |
| `POST /emby` | Emby webhook |
| `POST /tautulli` | Tautulli webhook |
| `POST /api/batch/submit` | Submit batch transcription via API |
| `GET /api/batch/sessions/{id}` | Get session status |
| `GET /docs` | OpenAPI/Swagger documentation |

# Supported Languages

SubGen-Azure-Batch supports 99+ languages via Azure Speech Services. See the [full list](https://learn.microsoft.com/azure/ai-services/speech-service/language-support?tabs=stt).

Common languages:
- English (en), Spanish (es), French (fr), German (de)
- Japanese (ja), Chinese (zh), Korean (ko)
- Portuguese (pt), Italian (it), Russian (ru)
- And many more...

# License

MIT License - see [LICENSE](LICENSE)

# Credits

SubGen-Azure-Batch is built on top of the original [SubGen](https://github.com/McCloudS/subgen) project by [McCloudS](https://github.com/McCloudS). 

The original SubGen project provides local Whisper-based subtitle generation and inspired the architecture and webhook integrations used in SubGen-Azure-Batch. We are grateful for McCloudS's work in creating and maintaining the original project.

**Consider supporting the original project:**

[![Donate to original subgen project](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/donate/?hosted_button_id=SU4QQP6LH5PF6)

**Additional credits:**
- [Azure Speech Services](https://azure.microsoft.com/services/cognitive-services/speech-services/) - Cloud transcription API
- [FFmpeg](https://ffmpeg.org/) - Audio extraction
- [Whisper ASR Webservice](https://github.com/ahmetoner/whisper-asr-webservice) - Bazarr webhook implementation reference
