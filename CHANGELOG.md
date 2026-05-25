# Changelog

All notable changes to SubGen-Azure-Batch are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

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
