# Changelog

All notable changes to SubGen-Azure-Batch are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Changed

- MCP is hosted at `/mcp` in Docker and Kubernetes, with updated deployment examples. An empty `AGENT_MCP_ALLOWED_HOSTS` now accepts any host/Origin while retaining mandatory API-key authentication; a configured allowlist enables DNS-rebinding protection.

### Fixed

- Bazarr subtitle scans are serialized across batches and webhooks and wait for running scheduled jobs. Uncertain scans pause further notifications until SubGen restarts; unmatched paths no longer launch asynchronous full-library update jobs.
- Bazarr SRT and language-detection responses now send configurable whitespace heartbeats during queueing and Azure processing. ASR processing remains timeout-bounded, disconnected requests clean up known resources, and MCP authentication no longer masks failed streams. Buffered responses remain available with `ASR_KEEPALIVE_INTERVAL=0`.
- Language detection now uses shared job tracking and the Bazarr-priority queue, honors configured forced languages, reports failures explicitly, and sends Azure v3.2 `languageIdentification` inside transcription properties.
- Bazarr/Kubernetes troubleshooting now distinguishes upload socket timeouts, response-idle timeouts, and connectivity failures; Uvicorn keep-alive documentation no longer claims to protect active requests.
- Bazarr scan timeouts now report an unconfirmed remote outcome with exception diagnostics instead of claiming the scan failed; batch completion notifications are sent before waiting for scans.
- Media-server refresh now runs after every saved video subtitle, including every Jellyfin episode in a shared season directory. Webhook jobs use the unified transcription service and release duplicate tracking when skipped.

### Added

- Session headers display transcription language names and codes for Web UI, Bazarr, webhook, and API batches, including multi-language batches.
- Batch notifications identify the batch, languages, and shows/seasons or movies, with bounded media and failed-file summaries.
- Per-server Plex/Jellyfin/Emby path mappings, exact-path Jellyfin item resolution, bounded refresh retries, and per-job queued/failure outcomes in the UI.
- Desktop and compact-screen Plex/Jellyfin connection badges backed by authenticated, read-only API checks using the configured environment credentials.

- Authenticated REST and MCP interfaces for secure agent-submitted transcription batches
- Per-batch languages, skip-rule and Bazarr notification options with path validation, detailed progress, and cancellation
- Pushover batch completion summaries for successes and batches with errors, including completed, failed, and skipped file counts. Success summaries are opt-in with `NOTIFY_ON_SUCCESS`.

## [1.0.1] - 2025-05-25

### Added

- Web UI for browsing media folders and submitting batch transcription jobs
- Azure Batch Transcription API v3.2 integration (no local GPU required)
- Bazarr-compatible `/asr` and `/detect-language` endpoints
- Plex, Jellyfin, Emby, and Tautulli webhook receivers
- Bazarr scan-disk notification after subtitle creation
- Pushover failure notifications
- Multi-architecture Docker image (linux/amd64, linux/arm64, ~500MB)
- Global concurrency limit with Bazarr priority queue
- Skip logic via FFprobe stream inspection
- Language detection with configurable candidate locales (max 4)
- LRC file generation for audio files
- Path mapping between media server and container paths

### Changed

- Forked from [SubGen](https://github.com/McCloudS/subgen) by McCloudS
- Replaced local Whisper processing with Azure Batch Transcription API
- Removed GPU/CUDA dependencies (image size: ~8GB → ~500MB)
- Media server metadata refresh now triggers after transcription completes (not at queue start)
- Subtitle naming respects `SUBTITLE_LANGUAGE_NAMING_TYPE` (ISO_639_1, ISO_639_2_T, ISO_639_2_B, NAME, NATIVE)

### Removed

- Local Whisper model support (`WHISPER_MODEL`, `TRANSCRIBE_DEVICE`, `WHISPER_THREADS`, `MODEL_PATH`)
- GPU/CUDA dependencies
- Standalone launcher (`launcher.py`) — Docker only
- Apprise notifications (replaced by Pushover)
