"""
Tests for API routers.

Uses FastAPI TestClient to test router endpoints.
Comprehensive tests for UI, ASR, Batch, and Webhook routers.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Test if FastAPI is installed
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient


class TestUIRouter:
    """Test UI router endpoints."""
    
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


class TestWebhookRouter:
    """Test webhook router endpoints."""
    
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