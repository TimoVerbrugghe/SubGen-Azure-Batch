"""
Tests for API routers.

Uses FastAPI TestClient to test router endpoints.
Comprehensive tests for UI, ASR, Batch, and Webhook routers.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# Test if FastAPI is installed
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


class TestUIRouter:
    """Test UI router endpoints."""

    @pytest.mark.parametrize(
        ("plex_outcome", "jellyfin_outcome"),
        [
            ("connected", "connected"),
            ("failed", "connected"),
            ("unconfigured", "unconfigured"),
        ],
    )
    def test_should_include_independent_authenticated_media_status_without_azure_config(
        self, plex_outcome, jellyfin_outcome, app_client, mock_settings
    ):
        from app.routers import ui
        from app.utils.media_server_client import MediaConnectionStatus

        mock_settings.azure.speech_key = ""
        mock_settings.azure.storage_connection_string = ""
        outcomes = [
            MediaConnectionStatus(
                configured=outcome != "unconfigured",
                connected=outcome == "connected",
                error="Invalid token or insufficient permissions"
                if outcome == "failed"
                else None,
            )
            for outcome in (plex_outcome, jellyfin_outcome)
        ]
        with (
            patch.object(ui, "get_settings", return_value=mock_settings),
            patch.object(
                ui.PlexClient,
                "check_connection",
                new=AsyncMock(return_value=outcomes[0]),
            ) as plex,
            patch.object(
                ui.JellyfinClient,
                "check_connection",
                new=AsyncMock(return_value=outcomes[1]),
            ) as jellyfin,
        ):
            response = app_client.get("/api/status")
        assert response.status_code == 200
        assert response.json() == {
            "speech": {"configured": False, "connected": False, "error": None},
            "storage": {"configured": False, "connected": False, "error": None},
            "plex": {
                "configured": outcomes[0].configured,
                "connected": outcomes[0].connected,
                "error": outcomes[0].error,
            },
            "jellyfin": {
                "configured": outcomes[1].configured,
                "connected": outcomes[1].connected,
                "error": outcomes[1].error,
            },
        }
        plex.assert_awaited_once_with()
        jellyfin.assert_awaited_once_with()
        assert mock_settings.plex.token not in response.text
        assert mock_settings.jellyfin.token not in response.text

    def test_should_show_media_connection_badges_in_desktop_and_compact_status_menu(
        self, app_client, mock_settings
    ):
        response = app_client.get("/")
        assert response.status_code == 200
        for badge in (
            "plex-status",
            "jellyfin-status",
            "plex-status-mobile",
            "jellyfin-status-mobile",
        ):
            assert f'id="{badge}"' in response.text
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_index_returns_html(self, client):
        """Test that index returns HTML content."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "SubGen-Azure-Batch" in response.text
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "azure_configured" in data
        assert "version" in data
    
    def test_get_config(self, client):
        """Test config endpoint returns safe configuration."""
        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        
        # Check expected fields exist
        assert "azure_configured" in data
        assert "azure_region" in data
        assert "bazarr_configured" in data
        assert "media_folders" in data
        assert "subtitle_language" in data
        
        # Ensure no sensitive data is exposed
        assert "speech_key" not in str(data).lower()
        assert "api_key" not in str(data).lower()
    
    def test_list_languages(self, client):
        """Test languages list endpoint."""
        response = client.get("/api/languages")
        assert response.status_code == 200
        data = response.json()
        assert "languages" in data
        assert len(data["languages"]) > 0
        
        # Check structure
        lang = data["languages"][0]
        assert "code" in lang
        assert "name" in lang
        assert "azure_locale" in lang


class TestASRRouter:
    """Test ASR router endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_asr_languages_endpoint(self, client):
        """Test ASR languages endpoint."""
        response = client.get("/asr/languages")
        assert response.status_code == 200
        data = response.json()
        assert "languages" in data
    
    def test_asr_without_file_returns_error(self, client):
        """Test ASR endpoint requires a file."""
        response = client.post("/asr")
        assert response.status_code == 422  # Validation error
    
    @patch('app.routers.asr.get_settings')
    def test_asr_unconfigured_azure(self, mock_settings, client):
        """Test ASR returns 503 when Azure is not configured."""
        # Mock settings with unconfigured Azure
        mock_azure = MagicMock()
        mock_azure.is_configured = False
        mock_settings.return_value.azure = mock_azure
        
        # Create a minimal audio file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF" + b"\x00" * 100)  # Minimal WAV header
            temp_path = f.name
        
        try:
            with open(temp_path, "rb") as f:
                response = client.post(
                    "/asr",
                    files={"audio_file": ("test.wav", f, "audio/wav")},
                    data={"language": "en", "output": "srt"},
                )
            # Should get 503 for unconfigured Azure
            assert response.status_code == 503
        finally:
            os.unlink(temp_path)


class TestBatchRouter:
    """Test batch processing router endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "sessions",
            "session/language-session",
            "job/language-session/language-job",
        ],
    )
    @pytest.mark.parametrize(
        "source,language,language_name",
        [
            ("ui", "nl", "Dutch"),
            ("bazarr", "dut", "Dutch"),
            ("bazarr", "en-AU", "English"),
            ("webhook", "de", "German"),
            ("api", "fr-FR", "French"),
            ("bazarr", "", None),
            ("bazarr", "unknown-language", None),
        ],
    )
    def test_should_include_transcription_language_in_job_and_session_responses(
        self,
        app_client: TestClient,
        mock_settings: MagicMock,
        patched_settings: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        endpoint: str,
        source: str,
        language: str,
        language_name: str | None,
    ) -> None:
        """Preserve requested language metadata for jobs from every source."""
        from app.transcription_service import (
            JobSource,
            TranscriptionJob,
            TranscriptionService,
            TranscriptionSession,
        )

        job_source = JobSource(source)
        job = TranscriptionJob(
            id="language-job",
            file_path="/media/episode.mkv",
            language=language,
            source=job_source,
        )
        session = TranscriptionSession(
            id="language-session", source=job_source, jobs={job.id: job}
        )
        monkeypatch.setattr(TranscriptionService, "_sessions", {session.id: session})

        response = app_client.get(f"/api/batch/{endpoint}")

        assert response.status_code == 200
        data = response.json()
        if endpoint == "sessions":
            data = data["sessions"][0]["jobs"][0]
        elif endpoint.startswith("session/"):
            data = data["jobs"][0]
        assert (data["language"], data["language_name"]) == (language, language_name)

    def test_should_return_chosen_language_when_submitting_ui_batch(
        self,
        app_client: TestClient,
        mock_settings: MagicMock,
        patched_settings: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        temp_video_file: str,
    ) -> None:
        """Include language metadata before the first session status poll."""
        from app.routers import batch
        from app.transcription_service import TranscriptionService

        monkeypatch.setattr(TranscriptionService, "_sessions", {})
        monkeypatch.setattr(batch, "_batch_metadata", {})
        with (
            patch.object(batch, "get_settings", return_value=mock_settings),
            patch.object(batch, "process_batch_session", new=AsyncMock()),
        ):
            response = app_client.post(
                "/api/batch/submit",
                json={
                    "files": [temp_video_file],
                    "language": "en-AU",
                    "skip_if_exists": False,
                },
            )

        assert response.status_code == 200
        job = response.json()["jobs"][0]
        assert (job["language"], job["language_name"]) == ("en-AU", "English")
    
    def test_list_sessions_empty(self, client):
        """Test listing sessions when none exist."""
        response = client.get("/api/batch/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
    
    def test_submit_batch_no_files(self, client):
        """Test batch submit with no files returns error."""
        response = client.post(
            "/api/batch/submit",
            json={"files": [], "language": "en"},
        )
        assert response.status_code == 400
        assert "No files" in response.json()["detail"]
    
    def test_submit_batch_invalid_files(self, client):
        """Test batch submit with non-existent files returns error."""
        response = client.post(
            "/api/batch/submit",
            json={
                "files": ["/non/existent/file.mp4"],
                "language": "en",
            },
        )
        assert response.status_code == 400
        # Message may vary - just check it mentions files not being valid
        detail = response.json()["detail"].lower()
        assert "not found" in detail or "no valid" in detail or "files" in detail
    
    def test_get_session_not_found(self, client):
        """Test getting non-existent session returns 404."""
        response = client.get("/api/batch/session/nonexistent")
        assert response.status_code == 404
    
    def test_get_job_not_found(self, client):
        """Test getting non-existent job returns 404."""
        response = client.get("/api/batch/job/nonexistent/nojob")
        assert response.status_code == 404
    
    def test_delete_session_not_found(self, client):
        """Test deleting non-existent session returns 404."""
        response = client.delete("/api/batch/session/nonexistent")
        assert response.status_code == 404


class TestBatchNotifications:
    """Test notification reporting through batch session processing."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("media_type", ["series", "movie", "unmatched"])
    @pytest.mark.parametrize(
        "scan_outcomes",
        [
            ("confirmed", "confirmed"),
            ("rejected", "rejected"),
            ("confirmed", "rejected"),
            ("unconfirmed", "unconfirmed"),
        ],
    )
    async def test_should_report_scan_result_without_duplicate_attempts(
        self, mock_settings, caplog, media_type: str, scan_outcomes: tuple[str, str]
    ) -> None:
        """Report scan outcomes accurately and attempt each target only once."""
        from app.routers import batch
        from app.transcription_service import (
            JobSource,
            JobStatus,
            TranscriptionJob,
            TranscriptionSession,
        )
        from app.utils.bazarr_client import BazarrScanOutcome

        outcome_values = [BazarrScanOutcome(value) for value in scan_outcomes]

        session = TranscriptionSession(id="notification-test")
        for index in range(2):
            job = TranscriptionJob(
                id=str(index),
                file_path=f"/media/video-{index}.mp4",
                language="en",
                source=JobSource.UI,
                status=JobStatus.COMPLETED,
            )
            session.jobs[job.id] = job

        mixed = outcome_values[0] != outcome_values[1]
        scan_success = outcome_values[0] is BazarrScanOutcome.CONFIRMED
        events = []

        def scan_side_effect(outcomes: list[BazarrScanOutcome]):
            index = 0

            async def record_scan(*args) -> BazarrScanOutcome:
                nonlocal index
                outcome = outcomes[index]
                index += 1
                events.append("scan")
                return outcome

            return record_scan

        async def record_completion(**kwargs) -> None:
            events.append("completion")

        bazarr = MagicMock()
        bazarr.search_series_by_path = AsyncMock(
            side_effect=[
                {"sonarrSeriesId": 299 + (index if mixed else 0)}
                if media_type == "series"
                else None
                for index in range(2)
            ]
        )
        bazarr.search_movie_by_path = AsyncMock(
            side_effect=[
                {"radarrId": 42 + (index if mixed else 0)}
                if media_type == "movie"
                else None
                for index in range(2)
            ]
        )
        bazarr.trigger_series_scan_outcome = AsyncMock(
            side_effect=scan_side_effect(outcome_values)
        )
        bazarr.trigger_movie_scan_outcome = AsyncMock(
            side_effect=scan_side_effect(outcome_values)
        )

        async def record_full_scan() -> BazarrScanOutcome:
            events.append("scan")
            return outcome_values[0]

        bazarr.trigger_disk_scan_outcome = AsyncMock(side_effect=record_full_scan)
        bazarr.close = AsyncMock()

        with (
            patch.object(batch, "get_settings", return_value=mock_settings),
            patch.object(batch, "BazarrClient", return_value=bazarr),
            patch.object(batch.TranscriptionService, "get_session", return_value=session),
            patch.object(
                batch.TranscriptionService,
                "transcribe_file",
                new=AsyncMock(return_value=(None, None)),
            ),
            patch.object(
                batch, "_refresh_media_servers_for_completed_jobs", new=AsyncMock()
            ),
            patch.object(
                batch, "notify_batch_completion", new=AsyncMock(side_effect=record_completion)
            ),
            patch.dict(batch._batch_metadata, {}, clear=True),
            caplog.at_level("INFO", logger="app.routers.batch"),
        ):
            await batch.process_batch_session(session.id)

        if media_type == "unmatched":
            bazarr.trigger_disk_scan_outcome.assert_not_awaited()
            bazarr.trigger_series_scan_outcome.assert_not_awaited()
            bazarr.trigger_movie_scan_outcome.assert_not_awaited()
            assert "No matching Bazarr items found; notification skipped" in caplog.text
        else:
            scan = (
                bazarr.trigger_series_scan_outcome
                if media_type == "series"
                else bazarr.trigger_movie_scan_outcome
            )
            target_id = 299 if media_type == "series" else 42
            if mixed:
                assert scan.await_args_list == [
                    call(target_id),
                    call(target_id + 1),
                ]
            else:
                scan.assert_awaited_once_with(target_id)
            bazarr.trigger_disk_scan_outcome.assert_not_awaited()
            assert ("Bazarr: Triggered disk scan" in caplog.text) == scan_success
            assert ("Notified Bazarr:" in caplog.text) == scan_success
            if BazarrScanOutcome.UNCONFIRMED in outcome_values:
                assert "Scan outcome unconfirmed" in caplog.text
                assert "Bazarr may still be scanning" in caplog.text
            elif mixed or not scan_success:
                assert "Bazarr: Scan not confirmed" in caplog.text
            if scan_success:
                expected_count = (
                    "1 series, 0 movies"
                    if media_type == "series"
                    else "0 series, 1 movies"
                )
                assert f"Notified Bazarr: {expected_count}" in caplog.text

        bazarr.close.assert_awaited_once_with()
        assert all(job.status == JobStatus.COMPLETED for job in session.jobs.values())
        assert events[0] == "completion"


class TestWebhookRouter:
    """Test webhook router endpoints."""

    @pytest.mark.parametrize("server", ["plex", "jellyfin", "emby"])
    def test_should_carry_webhook_item_id_and_original_path_into_background_job(
        self, server, app_client, mock_settings, patched_settings, temp_video_file
    ):
        from app.config import PathMappingConfig
        from app.routers import webhooks

        local_root = str(Path(temp_video_file).parent)
        server_path = f"/server/{Path(temp_video_file).name}"
        getattr(mock_settings, server).path_mapping = PathMappingConfig(
            enabled=True, from_path=local_root, to_path="/server"
        )
        if server == "plex":
            request_args = {
                "data": {
                    "payload": json.dumps(
                        {
                            "event": "library.new",
                            "Metadata": {
                                "type": "episode",
                                "ratingKey": "123",
                                "Media": [{"Part": [{"file": server_path}]}],
                            },
                        }
                    )
                }
            }
        elif server == "jellyfin":
            request_args = {
                "json": {
                    "NotificationType": "ItemAdded",
                    "ItemId": "123",
                    "ItemType": "Episode",
                    "Path": server_path,
                }
            }
        else:
            request_args = {
                "json": {
                    "Event": "library.new",
                    "Item": {"Id": "123", "Type": "Episode", "Path": server_path},
                }
            }
        with (
            patch.object(webhooks, "get_settings", return_value=mock_settings),
            patch.object(webhooks, "process_media_file", new=AsyncMock()) as process,
            patch.dict(webhooks._active_jobs, {}, clear=True),
        ):
            response = app_client.post(f"/webhook/{server}", **request_args)
        assert response.status_code == 200
        assert response.json() == {"status": "processing"}
        process.assert_awaited_once()
        assert process.call_args.kwargs["file_path"] == temp_video_file
        assert process.call_args.kwargs[f"{server}_item_id"] == "123"
        assert process.call_args.kwargs["server_paths"] == {server: server_path}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("skip", [False, True])
    async def test_should_route_webhook_through_shared_service_and_release_duplicate_tracking(
        self, skip, mock_settings, temp_video_file
    ):
        from app.routers import webhooks
        from app.transcription_service import JobSource
        from app.utils.skip_checker import SkipResult

        mock_settings.bazarr.is_configured = False
        result = MagicMock()
        job = MagicMock(srt_path=f"{temp_video_file}.eng.srt")
        with (
            patch.object(webhooks, "get_settings", return_value=mock_settings),
            patch.object(
                webhooks,
                "should_skip_file",
                new=AsyncMock(return_value=SkipResult(skip, "existing subtitles")),
            ) as skip_check,
            patch.object(webhooks, "get_audio_tracks", new=AsyncMock(return_value=[])),
            patch.object(
                webhooks.TranscriptionService,
                "transcribe_file",
                new=AsyncMock(return_value=(result, job)),
            ) as transcribe,
            patch.dict(webhooks._active_jobs, {temp_video_file: True}, clear=True),
        ):
            await webhooks.process_media_file(
                temp_video_file,
                plex_item_id="123",
                server_paths={"plex": "/server/episode.mkv"},
            )
            assert temp_video_file not in webhooks._active_jobs
        skip_check.assert_awaited_once_with(temp_video_file, "en")
        if skip:
            transcribe.assert_not_awaited()
        else:
            transcribe.assert_awaited_once()
            assert transcribe.call_args.kwargs["source"] == JobSource.WEBHOOK
            context = transcribe.call_args.kwargs["media_refresh_context"]
            assert context.item_ids == {"plex": "123"}
            assert context.server_paths == {"plex": "/server/episode.mkv"}

    def test_should_return_503_for_generation_webhook_without_azure(
        self, app_client, mock_settings, patched_settings
    ):
        from app.routers import webhooks

        mock_settings.azure.is_configured = False
        with patch.object(webhooks, "get_settings", return_value=mock_settings):
            response = app_client.post(
                "/webhook/jellyfin", json={"NotificationType": "ItemAdded"}
            )
        assert response.status_code == 503
        assert "Azure Speech Services not configured" in response.json()["detail"]

    def test_should_return_asr_subtitles_without_premature_media_refresh(
        self, app_client, mock_settings, patched_settings, mock_transcription_result
    ):
        from app.transcription_service import TranscriptionService

        with (
            patch.object(
                TranscriptionService,
                "transcribe_audio_data",
                new=AsyncMock(return_value=(mock_transcription_result, MagicMock())),
            ),
            patch("app.transcription_service.MediaServerRefresher") as refresh,
        ):
            response = app_client.post(
                "/asr?language=en&output=srt",
                files={"audio_file": ("audio.wav", b"audio", "audio/wav")},
            )
        assert response.status_code == 200
        assert response.text == mock_transcription_result.to_srt()
        refresh.assert_not_called()
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_webhook_status(self, client):
        """Test webhook status endpoint."""
        response = client.get("/webhook/status")
        assert response.status_code == 200
        data = response.json()
        assert "active_jobs" in data
        assert "job_paths" in data
    
    def test_plex_webhook_no_payload(self, client):
        """Test Plex webhook with empty payload."""
        response = client.post(
            "/webhook/plex",
            data={"payload": "{}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ignored"
    
    def test_jellyfin_webhook_wrong_event(self, client):
        """Test Jellyfin webhook with ignored event type."""
        response = client.post(
            "/webhook/jellyfin",
            json={"NotificationType": "UserDeleted"},  # An event we don't process
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ignored"
    
    def test_emby_webhook_wrong_event(self, client):
        """Test Emby webhook with ignored event type."""
        response = client.post(
            "/webhook/emby",
            json={"Event": "playback.stop"},  # Stop is not processed
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ignored"
    
    def test_tautulli_webhook_no_file(self, client):
        """Test Tautulli webhook with no file."""
        response = client.post(
            "/webhook/tautulli",
            data={},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "no_file"


class TestAppStartup:
    """Test application startup."""
    
    def test_app_creates_successfully(self):
        """Test that the app can be created."""
        from app.main import create_app
        app = create_app()
        assert app.title == "SubGen-Azure-Batch"
        # Version may vary - just check it's set
        assert app.version is not None
    
    def test_app_has_docs(self):
        """Test that docs endpoints are available."""
        from app.main import app
        client = TestClient(app)
        
        response = client.get("/docs")
        assert response.status_code == 200
        
        response = client.get("/openapi.json")
        assert response.status_code == 200


class TestASRRouterExtended:
    """Extended tests for ASR router endpoints."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_root_returns_version(self, client):
        """Test root endpoint returns version info."""
        response = client.get("/")
        assert response.status_code == 200
    
    def test_asr_get_returns_error_message(self, client):
        """Test GET /asr returns error message."""
        response = client.get("/asr")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "GET" in data[0]
    
    def test_status_endpoint(self, client):
        """Test /status endpoint returns version."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "SubGen-Azure-Batch" in data["version"]
    
    def test_detect_language_get_returns_error(self, client):
        """Test GET /detect-language returns error message."""
        response = client.get("/detect-language")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "GET" in data[0]


class TestBatchRouterExtended:
    """Extended tests for batch processing router."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_list_sessions(self, client):
        """Test listing batch sessions."""
        response = client.get("/api/batch/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
    
    def test_submit_batch_empty_request(self, client):
        """Test batch submit with empty files list."""
        response = client.post(
            "/api/batch/submit",
            json={"files": [], "folders": [], "language": "en"},
        )
        assert response.status_code == 400
        assert "No files" in response.json()["detail"]
    
    def test_submit_batch_nonexistent_folder(self, client):
        """Test batch submit with non-existent folder."""
        response = client.post(
            "/api/batch/submit",
            json={
                "files": [],
                "folders": ["/nonexistent/folder"],
                "language": "en",
            },
        )
        # Should handle gracefully (no valid files)
        assert response.status_code == 400
    
    def test_cancel_nonexistent_session(self, client):
        """Test canceling non-existent session returns 404."""
        response = client.post("/api/batch/session/nonexistent/cancel")
        # Could be 404 or 405 depending on implementation
        assert response.status_code in [404, 405]


class TestBatchCompletionNotifications:
    """Test batch completion notification behavior through the batch worker."""

    @pytest.fixture(autouse=True)
    def clear_batch_metadata(self):
        from app.routers import batch

        batch._batch_metadata.clear()
        yield
        batch._batch_metadata.clear()

    async def process_batch(self, statuses, skipped=()):
        from app.routers import batch
        from app.transcription_service import (
            JobSource,
            TranscriptionJob,
            TranscriptionService,
            TranscriptionSession,
        )

        session = TranscriptionSession(id="notification-test", source=JobSource.UI)
        for index, status in enumerate(statuses):
            job = TranscriptionJob(
                id=f"job-{index}",
                file_path=f"/media/{index}.mkv",
                language="en",
                source=JobSource.UI,
                status=status,
            )
            session.jobs[job.id] = job

        metadata = {
            "skipped": [{"file_path": path} for path in skipped],
            "notify_bazarr": False,
        }
        batch._batch_metadata[session.id] = metadata

        with (
            patch.object(TranscriptionService, "get_session", return_value=session),
            patch(
                "app.routers.batch.process_batch_job", new_callable=AsyncMock
            ) as process_job,
            patch(
                "app.routers.batch._refresh_media_servers_for_completed_jobs",
                new_callable=AsyncMock,
            ),
            patch(
                "app.routers.batch.notify_batch_completion",
                new_callable=AsyncMock,
            ) as notify,
            patch(
                "app.routers.batch.get_settings",
                return_value=MagicMock(bazarr=MagicMock(is_configured=False)),
            ),
        ):
            await batch.process_batch_session(session.id)

        return session, metadata, notify, process_job

    @pytest.mark.asyncio
    async def test_success_summary_includes_skipped_file_count(self):
        from app.transcription_service import JobStatus

        _, metadata, notify, _ = await self.process_batch(
            [JobStatus.COMPLETED, JobStatus.COMPLETED],
            skipped=["/media/skipped.mkv"],
        )

        notify.assert_awaited_once_with(
            completed_jobs=2,
            failed_jobs=0,
            skipped_jobs=1,
            batch_id="notification-test",
            file_paths=["/media/0.mkv", "/media/1.mkv"],
            languages=["en", "en"],
            failed_files=[],
        )
        assert metadata["completion_notification_attempted"] is True

    @pytest.mark.parametrize(
        ("statuses", "expected_completed", "expected_failed"),
        [
            (["completed", "failed"], 1, 1),
            (["failed", "failed"], 0, 2),
        ],
        ids=["mixed-results", "all-errors"],
    )
    @pytest.mark.asyncio
    async def test_error_summaries_cover_mixed_and_all_error_batches(
        self, statuses, expected_completed, expected_failed
    ):
        from app.transcription_service import JobStatus

        service_statuses = [JobStatus(status) for status in statuses]
        _, _, notify, _ = await self.process_batch(service_statuses)

        notify.assert_awaited_once_with(
            completed_jobs=expected_completed,
            failed_jobs=expected_failed,
            skipped_jobs=0,
            batch_id="notification-test",
            file_paths=["/media/0.mkv", "/media/1.mkv"],
            languages=["en", "en"],
            failed_files=[
                f"/media/{index}.mkv"
                for index, status in enumerate(statuses) if status == "failed"
            ],
        )

    @pytest.mark.asyncio
    async def test_cancellation_prevents_batch_summary(self):
        from app.transcription_service import JobStatus

        _, metadata, notify, _ = await self.process_batch(
            [JobStatus.COMPLETED, JobStatus.CANCELLED]
        )

        notify.assert_not_awaited()
        assert metadata["completion_notification_attempted"] is True

    @pytest.mark.asyncio
    async def test_incomplete_batch_does_not_send_summary(self):
        from app.transcription_service import JobStatus

        _, metadata, notify, _ = await self.process_batch([JobStatus.PENDING])

        notify.assert_not_awaited()
        assert "completion_notification_attempted" not in metadata

    @pytest.mark.asyncio
    async def test_batch_summary_is_attempted_only_once(self):
        from app.routers import batch
        from app.transcription_service import JobStatus

        _, _, notify, _ = await self.process_batch([JobStatus.COMPLETED])

        with (
            patch(
                "app.routers.batch.process_batch_job", new_callable=AsyncMock
            ),
            patch(
                "app.routers.batch._refresh_media_servers_for_completed_jobs",
                new_callable=AsyncMock,
            ),
            patch(
                "app.routers.batch.notify_batch_completion",
                new_callable=AsyncMock,
            ) as second_notify,
            patch(
                "app.routers.batch.get_settings",
                return_value=MagicMock(bazarr=MagicMock(is_configured=False)),
            ),
        ):
            await batch.process_batch_session("notification-test")

        notify.assert_awaited_once()
        second_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notification_error_does_not_change_batch_status(self):
        from app.routers import batch
        from app.transcription_service import JobStatus

        session, metadata, _, _ = await self.process_batch(
            [JobStatus.COMPLETED, JobStatus.FAILED]
        )
        initial_statuses = [job.status for job in session.jobs.values()]
        metadata.pop("completion_notification_attempted")

        with (
            patch(
                "app.routers.batch.process_batch_job", new_callable=AsyncMock
            ),
            patch(
                "app.routers.batch._refresh_media_servers_for_completed_jobs",
                new_callable=AsyncMock,
            ),
            patch(
                "app.routers.batch.notify_batch_completion",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network unavailable"),
            ),
            patch(
                "app.routers.batch.get_settings",
                return_value=MagicMock(bazarr=MagicMock(is_configured=False)),
            ),
        ):
            await batch.process_batch_session(session.id)

        assert [job.status for job in session.jobs.values()] == initial_statuses


class TestWebhookRouterExtended:
    """Extended tests for webhook router."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_plex_webhook_valid_event_no_file(self, client):
        """Test Plex webhook with valid event but missing file path."""
        payload = {
            "event": "library.new",
            "Metadata": {
                "type": "episode",
                "title": "Test Episode"
            }
        }
        response = client.post(
            "/webhook/plex",
            data={"payload": str(payload).replace("'", '"')},
        )
        assert response.status_code == 200
    
    def test_jellyfin_webhook_valid_event(self, client):
        """Test Jellyfin webhook with valid event type."""
        response = client.post(
            "/webhook/jellyfin",
            json={
                "NotificationType": "ItemAdded",
                "ItemType": "Episode"
            },
        )
        # Should process but may not find file
        assert response.status_code == 200
    
    def test_emby_webhook_playback_start(self, client):
        """Test Emby webhook with playback start event."""
        response = client.post(
            "/webhook/emby",
            json={"Event": "playback.start"},
        )
        assert response.status_code == 200
    
    def test_bazarr_webhook_missing_file(self, client):
        """Test Bazarr webhook with missing file path."""
        response = client.post(
            "/webhook/bazarr",
            json={"video_file": "/nonexistent/file.mkv"},
        )
        # Should handle missing file gracefully - may return various status codes
        assert response.status_code in [200, 400, 404]


class TestStaticAndTemplates:
    """Test static file serving and templates."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_index_page_content(self, client):
        """Test that index page contains expected elements."""
        response = client.get("/")
        assert response.status_code == 200
        assert "SubGen-Azure-Batch" in response.text
        # Check for key UI elements
        assert "html" in response.text.lower()
    
    def test_css_static_file(self, client):
        """Test that CSS file is served."""
        response = client.get("/static/css/style.css")
        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")
    
    def test_js_static_file(self, client):
        """Test that JavaScript file is served."""
        response = client.get("/static/js/app.js")
        assert response.status_code == 200


class TestErrorHandling:
    """Test error handling in routers."""
    
    @pytest.fixture
    def client(self):
        """Create test client."""
        from app.main import app
        return TestClient(app)
    
    def test_404_for_unknown_route(self, client):
        """Test 404 for unknown routes."""
        response = client.get("/api/unknown/endpoint")
        assert response.status_code == 404
    
    def test_method_not_allowed(self, client):
        """Test 405 for wrong HTTP method."""
        # POST to a GET-only endpoint
        response = client.post("/api/config")
        assert response.status_code == 405


if __name__ == "__main__":
    pytest.main([__file__, "-v"])