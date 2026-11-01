"""Tests for authenticated agent REST and MCP batch interfaces."""

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.routers import batch as batch_router
from app.transcription_service import JobSource, JobStatus, TranscriptionService
from app.utils.skip_checker import SkipResult

MEDIA_ROOT = Path(__file__).parent / "data" / "agent-media"
OUTSIDE_ROOT = Path(__file__).parent / "data" / "agent-outside"
API_KEY = "subgen-agent-key-0123456789abcdef"


@pytest.fixture
def agent_client(mock_settings, monkeypatch):
    """Create an authenticated app client with an isolated media root."""
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
    shutil.rmtree(OUTSIDE_ROOT, ignore_errors=True)
    MEDIA_ROOT.mkdir(parents=True)
    OUTSIDE_ROOT.mkdir(parents=True)
    mock_settings.media_folders = [str(MEDIA_ROOT)]
    mock_settings.agent_api_key = API_KEY

    monkeypatch.setattr("app.agent_api.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.main.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.config.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.agent_api._start_batch_task", lambda session_id: None)

    TranscriptionService._sessions.clear()
    batch_router._batch_metadata.clear()
    from app.main import app

    client = TestClient(app)
    yield client
    client.close()

    TranscriptionService._sessions.clear()
    batch_router._batch_metadata.clear()
    shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
    shutil.rmtree(OUTSIDE_ROOT, ignore_errors=True)


def _write_media(path: Path) -> Path:
    """Create a small test media placeholder under the project test tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test media")
    return path


def _headers() -> dict:
    """Return the test API key header."""
    return {"X-API-Key": API_KEY}


def _mcp_data(response) -> dict:
    """Decode JSON or the JSON event from a Streamable HTTP response."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        raise AssertionError(f"No MCP data event returned: {response.text}")
    return response.json()


class TestAgentBatchRest:
    """Exercise agent batch submission, status, authorization, and cancellation."""

    def test_submit_folders_files_languages_and_request_options(self, agent_client):
        nested_file = _write_media(MEDIA_ROOT / "Series" / "Season 1" / "one.mkv")
        _write_media(MEDIA_ROOT / "Series" / "Season 1" / "two.mp3")
        direct_file = _write_media(MEDIA_ROOT / "Movies" / "movie.mp4")

        with patch(
            "app.agent_api.should_skip_file",
            new_callable=AsyncMock,
        ) as skip_check:
            response = agent_client.post(
                "/api/agent/batches",
                headers=_headers(),
                json={
                    "folders": [str(MEDIA_ROOT / "Series")],
                    "files": [str(direct_file)],
                    "languages": ["en", "fr-FR"],
                    "apply_skip_rules": False,
                    "notify_bazarr": True,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_count"] == 6
        assert {job["language"] for job in data["jobs"]} == {"en-US", "fr-FR"}
        assert {job["file_path"] for job in data["jobs"]} == {
            str(nested_file),
            str(MEDIA_ROOT / "Series" / "Season 1" / "two.mp3"),
            str(direct_file),
        }
        assert all(job["stage"] == "queued" for job in data["jobs"])
        skip_check.assert_not_awaited()

        session = TranscriptionService.get_session(data["session_id"])
        assert session is not None
        assert session.source == JobSource.API
        assert session.notify_bazarr is True
        assert batch_router._batch_metadata[data["session_id"]]["notify_bazarr"] is True

        active = agent_client.get("/api/agent/batches", headers=_headers())
        assert active.status_code == 200
        assert len(active.json()["batches"]) == 1
        assert active.json()["batches"][0]["pending"] == 6

        detail = agent_client.get(
            f"/api/agent/batches/{data['session_id']}", headers=_headers()
        )
        assert detail.status_code == 200
        assert detail.json()["jobs"][0]["stage"] == "queued"
        assert detail.json()["jobs"][0]["language"] in {"en-US", "fr-FR"}

    def test_skip_rules_are_applied_per_requested_language(self, agent_client):
        media_file = _write_media(MEDIA_ROOT / "skip.mkv")

        async def skip_english(path: str, language: str) -> SkipResult:
            assert path == str(media_file)
            return (
                SkipResult.skip("existing subtitle")
                if language == "en-US"
                else SkipResult.proceed()
            )

        with patch(
            "app.agent_api.should_skip_file", side_effect=skip_english
        ) as checker:
            response = agent_client.post(
                "/api/agent/batches",
                headers=_headers(),
                json={
                    "files": [str(media_file)],
                    "languages": ["en", "de"],
                    "apply_skip_rules": True,
                    "notify_bazarr": False,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["job_count"] == 1
        assert data["jobs"][0]["language"] == "de-DE"
        assert data["skipped"] == [
            {
                "file_path": str(media_file),
                "language": "en-US",
                "reason": "existing subtitle",
            }
        ]
        assert checker.await_count == 2
        session = TranscriptionService.get_session(data["session_id"])
        assert session is not None and session.notify_bazarr is False

    def test_batch_options_are_scoped_to_each_request(self, agent_client):
        first_file = _write_media(MEDIA_ROOT / "first.mkv")
        second_file = _write_media(MEDIA_ROOT / "second.mkv")
        with patch(
            "app.agent_api.should_skip_file",
            new_callable=AsyncMock,
            return_value=SkipResult.proceed(),
        ):
            first = agent_client.post(
                "/api/agent/batches",
                headers=_headers(),
                json={
                    "files": [str(first_file)],
                    "languages": ["en"],
                    "apply_skip_rules": False,
                    "notify_bazarr": True,
                },
            ).json()
            second = agent_client.post(
                "/api/agent/batches",
                headers=_headers(),
                json={
                    "files": [str(second_file)],
                    "languages": ["de"],
                    "apply_skip_rules": True,
                    "notify_bazarr": False,
                },
            ).json()

        first_session = TranscriptionService.get_session(first["session_id"])
        second_session = TranscriptionService.get_session(second["session_id"])
        assert first_session is not None and first_session.notify_bazarr is True
        assert second_session is not None and second_session.notify_bazarr is False
        assert (
            batch_router._batch_metadata[first["session_id"]]["notify_bazarr"] is True
        )
        assert (
            batch_router._batch_metadata[second["session_id"]]["notify_bazarr"] is False
        )

    @pytest.mark.parametrize(
        "request_data",
        [
            {"files": ["/outside/private.mkv"], "languages": ["en"]},
            {"files": [str(MEDIA_ROOT / ".." / "private.mkv")], "languages": ["en"]},
            {"files": [str(MEDIA_ROOT / "missing.mkv")], "languages": ["en"]},
            {"files": [str(MEDIA_ROOT / "bad.txt")], "languages": ["en"]},
            {"files": [str(MEDIA_ROOT / "valid.mkv")], "languages": ["bad-locale"]},
            {"files": [str(MEDIA_ROOT / "valid.mkv")], "languages": ["en"] * 5},
        ],
    )
    def test_invalid_paths_and_languages_are_rejected_before_scheduling(
        self, agent_client, request_data, monkeypatch
    ):
        _write_media(MEDIA_ROOT / "valid.mkv")
        scheduled = []
        monkeypatch.setattr(
            "app.agent_api._start_batch_task",
            lambda session_id: scheduled.append(session_id),
        )

        response = agent_client.post(
            "/api/agent/batches",
            headers=_headers(),
            json=request_data,
        )

        assert response.status_code == 400
        assert scheduled == []
        assert TranscriptionService.list_all_sessions() == []

    def test_symlink_escape_is_rejected(self, agent_client):
        outside = _write_media(OUTSIDE_ROOT / "secret.mkv")
        link = MEDIA_ROOT / "escape.mkv"
        link.symlink_to(outside)

        response = agent_client.post(
            "/api/agent/batches",
            headers=_headers(),
            json={"files": [str(link)], "languages": ["en"]},
        )

        assert response.status_code == 400
        assert "outside configured" in response.json()["detail"]

    def test_authentication_fails_closed_for_missing_or_invalid_key(self, agent_client):
        payload = {"files": [str(MEDIA_ROOT / "media.mkv")], "languages": ["en"]}
        _write_media(MEDIA_ROOT / "media.mkv")

        missing = agent_client.post("/api/agent/batches", json=payload)
        invalid = agent_client.post(
            "/api/agent/batches",
            headers={"X-API-Key": "wrong-key"},
            json=payload,
        )

        assert missing.status_code == 401
        assert invalid.status_code == 401

    @pytest.mark.parametrize(
        "configured_key",
        ["", "too-short", f"{'a' * 16} {'b' * 15}"],
    )
    def test_agent_api_is_disabled_without_a_strong_key(
        self, agent_client, mock_settings, configured_key
    ):
        mock_settings.agent_api_key = configured_key
        response = agent_client.get("/api/agent/batches", headers=_headers())
        assert response.status_code == 503

    def test_unknown_batch_is_not_found(self, agent_client):
        response = agent_client.get(
            "/api/agent/batches/unknown-batch", headers=_headers()
        )
        cancelled = agent_client.post(
            "/api/agent/batches/unknown-batch/cancel", headers=_headers()
        )
        assert response.status_code == cancelled.status_code == 404

    def test_batch_detail_reports_each_processing_stage(self, agent_client):
        media_files = [
            _write_media(MEDIA_ROOT / f"stage-{index}.mkv") for index in range(7)
        ]
        submitted = agent_client.post(
            "/api/agent/batches",
            headers=_headers(),
            json={
                "files": [str(path) for path in media_files],
                "languages": ["en"],
                "apply_skip_rules": False,
            },
        ).json()
        session = TranscriptionService.get_session(submitted["session_id"])
        assert session is not None
        statuses = [
            JobStatus.PENDING,
            JobStatus.EXTRACTING,
            JobStatus.UPLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ]
        for job, status in zip(session.jobs.values(), statuses):
            job.status = status

        response = agent_client.get(
            f"/api/agent/batches/{submitted['session_id']}", headers=_headers()
        )

        assert response.status_code == 200
        data = response.json()
        assert {job["stage"] for job in data["jobs"]} == {
            "queued",
            "extracting",
            "uploading",
            "transcribing",
            "completed",
            "failed",
            "cancelled",
        }
        assert data["pending"] == 1
        assert data["in_progress"] == 3
        assert data["completed"] == data["failed"] == data["cancelled"] == 1

    def test_cancel_batch_updates_status_and_returns_cleanup_summary(
        self, agent_client
    ):
        media_file = _write_media(MEDIA_ROOT / "cancel.mkv")
        submitted = agent_client.post(
            "/api/agent/batches",
            headers=_headers(),
            json={"files": [str(media_file)], "languages": ["en"]},
        ).json()

        response = agent_client.post(
            f"/api/agent/batches/{submitted['session_id']}/cancel",
            headers=_headers(),
        )

        assert response.status_code == 200
        assert response.json()["cancelled_jobs"] == 1
        status = agent_client.get(
            f"/api/agent/batches/{submitted['session_id']}", headers=_headers()
        ).json()
        assert status["cancelled"] == 1
        assert status["jobs"][0]["stage"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_stops_a_tracked_worker_task(mock_settings, monkeypatch):
    """Cancelling through the shared batch interface also stops its worker."""
    from app.agent_api import _agent_batch_tasks, cancel_agent_batch

    monkeypatch.setattr("app.agent_api.get_settings", lambda: mock_settings)
    monkeypatch.setattr("app.config.get_settings", lambda: mock_settings)
    session = await TranscriptionService.create_session(
        source=JobSource.API,
        notify_bazarr=False,
    )
    await TranscriptionService.add_job(
        session.id, "/media/pending.mkv", "en-US", JobSource.API
    )
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    _agent_batch_tasks[session.id] = task
    await started.wait()

    result = await cancel_agent_batch(session.id)

    assert result.status == "cancelled"
    assert result.cancelled_jobs == 1
    assert task.cancelled()
    _agent_batch_tasks.pop(session.id, None)
    TranscriptionService._sessions.pop(session.id, None)


class TestMCPProtocol:
    """Exercise initialization, discovery, and tool calls over real MCP HTTP."""

    def test_streamable_http_initialization_discovery_and_tool_calls(
        self, agent_client
    ):
        media_file = _write_media(MEDIA_ROOT / "mcp.mkv")
        agent_client.__enter__()
        headers = {
            "Host": "localhost:9000",
            "X-API-Key": API_KEY,
            "Accept": "application/json, text/event-stream",
        }
        initialize = agent_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-api-test", "version": "1.0"},
                },
            },
        )

        assert initialize.status_code == 200
        session_id = initialize.headers["mcp-session-id"]
        response_headers = {**headers, "Mcp-Session-Id": session_id}
        initialized = agent_client.post(
            "/mcp",
            headers=response_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert initialized.status_code == 202

        tools_response = agent_client.post(
            "/mcp",
            headers=response_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            },
        )
        assert tools_response.status_code == 200
        tools = _mcp_data(tools_response)["result"]["tools"]
        tools_by_name = {tool["name"]: tool for tool in tools}
        assert set(tools_by_name) == {
            "submit_transcription_batch",
            "list_active_batches",
            "get_batch_status",
            "cancel_batch",
        }
        assert "outputSchema" in tools_by_name["submit_transcription_batch"]
        assert (
            "languages"
            in tools_by_name["submit_transcription_batch"]["inputSchema"]["required"]
        )

        submit = agent_client.post(
            "/mcp",
            headers=response_headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "submit_transcription_batch",
                    "arguments": {
                        "files": [str(media_file)],
                        "languages": ["en-US", "de"],
                        "apply_skip_rules": False,
                        "notify_bazarr": True,
                    },
                },
            },
        )
        assert submit.status_code == 200
        submit_result = _mcp_data(submit)["result"]
        structured = submit_result.get("structuredContent")
        assert structured is not None
        assert structured["job_count"] == 2
        session = structured["session_id"]

        status = agent_client.post(
            "/mcp",
            headers=response_headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "get_batch_status",
                    "arguments": {"session_id": session},
                },
            },
        )
        assert status.status_code == 200
        status_result = _mcp_data(status)["result"]["structuredContent"]
        assert len(status_result["jobs"]) == 2
        assert {job["stage"] for job in status_result["jobs"]} == {"queued"}

    def test_mcp_is_protected_by_the_agent_key(self, agent_client):
        no_key = agent_client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert no_key.status_code == 401
