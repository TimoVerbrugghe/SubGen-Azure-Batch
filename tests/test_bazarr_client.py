"""
Tests for Bazarr API client.

Tests cover:
- Client initialization
- Connection testing
- Series/Movie scan triggering
- Path-based lookups
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from weakref import WeakKeyDictionary

import pytest
import aiohttp

from app.utils.bazarr_client import (
    BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS,
    BAZARR_SCAN_TIMEOUT_SECONDS,
    BazarrScanOutcome,
    BazarrClient,
)


@pytest.fixture(autouse=True)
def idle_bazarr_tasks(mock_aiohttp_session, mock_settings, monkeypatch) -> None:
    """Provide Bazarr's scheduled-job status separately from scan responses."""
    monkeypatch.setattr("app.utils.bazarr_client._scan_states", WeakKeyDictionary())
    monkeypatch.setattr("app.utils.bazarr_client.get_settings", lambda: mock_settings)
    response = AsyncMock()
    response.status = 200
    response.json.return_value = {
        "data": [{"job_id": "update_series", "job_running": False}]
    }
    response.__aenter__.return_value = response
    mock_aiohttp_session.get = MagicMock(return_value=response)


class TestBazarrClientInit:
    """Test BazarrClient initialization."""
    
    def test_client_initialization(self, mock_settings):
        """Test Bazarr client initializes correctly."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            
            assert client.url == mock_settings.bazarr.url
            assert client.api_key == mock_settings.bazarr.api_key
    
    def test_client_with_custom_params(self, mock_settings):
        """Test client with custom URL and API key."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient(
                url="http://custom:6767",
                api_key="custom-key"
            )
            
            assert client.url == "http://custom:6767"
            assert client.api_key == "custom-key"
    
    def test_url_trailing_slash_stripped(self, mock_settings):
        """Test that trailing slashes are removed from URL."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = "http://localhost:6767/"
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            
            assert client.url == "http://localhost:6767"


class TestBazarrClientConfig:
    """Test BazarrClient configuration checking."""
    
    def test_is_configured_when_both_set(self, mock_settings):
        """Test is_configured returns True when URL and key are set."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            assert client.is_configured is True
    
    def test_is_configured_when_url_missing(self, mock_settings):
        """Test is_configured returns False when URL is missing."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            assert client.is_configured is False
    
    def test_is_configured_when_key_missing(self, mock_settings):
        """Test is_configured returns False when API key is missing."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.api_key = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            assert client.is_configured is False


class TestBazarrClientHeaders:
    """Test BazarrClient headers property."""
    
    def test_headers_include_api_key(self, mock_settings):
        """Test that headers include the API key."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            headers = client.headers
            
            assert 'X-API-KEY' in headers
            assert headers['X-API-KEY'] == mock_settings.bazarr.api_key
            assert headers['Content-Type'] == 'application/json'


class TestBazarrClientConnection:
    """Test BazarrClient connection testing."""
    
    @pytest.mark.asyncio
    async def test_test_connection_not_configured(self, mock_settings):
        """Test connection returns False when not configured."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            result = await client.test_connection()
            assert result is False
    
    @pytest.mark.asyncio
    async def test_test_connection_success(self, mock_settings, mock_aiohttp_session):
        """Test successful connection test."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.get = MagicMock(return_value=mock_response)
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            result = await client.test_connection()
            assert result is True
    
    @pytest.mark.asyncio
    async def test_test_connection_failure(self, mock_settings, mock_aiohttp_session):
        """Test connection failure returns False."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_response = AsyncMock()
        mock_response.status = 401  # Unauthorized
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.get = MagicMock(return_value=mock_response)
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            result = await client.test_connection()
            assert result is False


class TestBazarrClientSeriesScan:
    """Test BazarrClient series scan triggering."""
    
    @pytest.mark.asyncio
    async def test_trigger_series_scan_not_configured(self, mock_settings):
        """Test series scan returns False when not configured."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            result = await client.trigger_series_scan(series_id=123)
            assert result is False
    
    @pytest.mark.asyncio
    async def test_trigger_series_scan_with_id(self, mock_settings, mock_aiohttp_session):
        """Test triggering series scan with specific ID."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_response = AsyncMock()
        mock_response.status = 204
        mock_response.text = AsyncMock(return_value="")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.patch = MagicMock(return_value=mock_response)
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            result = await client.trigger_series_scan(series_id=123)
            assert result is True
            mock_aiohttp_session.patch.assert_called_once()
            assert (
                mock_aiohttp_session.patch.call_args.kwargs["timeout"].total
                == BAZARR_SCAN_TIMEOUT_SECONDS
            )
            assert (
                mock_aiohttp_session.patch.call_args.kwargs["timeout"].sock_connect
                == BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS
            )

    @pytest.mark.asyncio
    async def test_trigger_series_scan_timeout_is_unconfirmed_with_diagnostics(
        self, mock_settings, mock_aiohttp_session, caplog
    ):
        """A timeout is unconfirmed because Bazarr may continue processing."""
        from app.utils.bazarr_client import BazarrClient

        mock_aiohttp_session.patch = MagicMock(side_effect=asyncio.TimeoutError())

        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session

            with caplog.at_level("ERROR", logger="app.utils.bazarr_client"):
                result = await client.trigger_series_scan_outcome(series_id=123)

        assert result is BazarrScanOutcome.UNCONFIRMED
        assert "series 123" in caplog.text
        assert "outcome unconfirmed" in caplog.text
        assert "Bazarr may still be scanning" in caplog.text
        assert f"timeout limit {BAZARR_SCAN_TIMEOUT_SECONDS}s" in caplog.text
        assert (
            f"connection limit {BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS}s" in caplog.text
        )
        assert "TimeoutError" in caplog.text

    @pytest.mark.asyncio
    async def test_trigger_series_scan_http_error_is_rejected(
        self, mock_settings, mock_aiohttp_session
    ):
        """An explicit HTTP rejection is distinct from an unknown outcome."""
        from app.utils.bazarr_client import BazarrClient

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="server error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.patch = MagicMock(return_value=mock_response)

        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session

            result = await client.trigger_series_scan_outcome(series_id=123)

        assert result is BazarrScanOutcome.REJECTED
    
    @pytest.mark.asyncio
    async def test_should_skip_full_series_update(self, mock_settings, mock_aiohttp_session):
        """Library-wide scheduled jobs must not be launched automatically."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_response = AsyncMock()
        mock_response.status = 204
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.post = MagicMock(return_value=mock_response)
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            result = await client.trigger_series_scan()  # No series_id
            assert result is False
            mock_aiohttp_session.post.assert_not_called()


class TestBazarrClientMovieScan:
    """Test BazarrClient movie scan triggering."""
    
    @pytest.mark.asyncio
    async def test_trigger_movie_scan_not_configured(self, mock_settings):
        """Test movie scan returns False when not configured."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            result = await client.trigger_movie_scan(movie_id=456)
            assert result is False
    
    @pytest.mark.asyncio
    async def test_trigger_movie_scan_with_id(self, mock_settings, mock_aiohttp_session):
        """Test triggering movie scan with specific ID."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value="")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.patch = MagicMock(return_value=mock_response)
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            result = await client.trigger_movie_scan(movie_id=456)
            assert result is True


class TestBazarrClientDiskScan:
    """Test BazarrClient disk scan triggering."""
    
    @pytest.mark.asyncio
    async def test_trigger_disk_scan_not_configured(self, mock_settings):
        """Test disk scan returns False when not configured."""
        from app.utils.bazarr_client import BazarrClient
        
        mock_settings.bazarr.url = ""
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            result = await client.trigger_disk_scan()
            assert result is False

    @pytest.mark.asyncio
    async def test_should_skip_full_disk_scan_when_configured(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """Full scans must not launch asynchronous series and movie jobs."""
        client = BazarrClient()
        client._session = mock_aiohttp_session
        assert (
            await client.trigger_disk_scan_outcome() is BazarrScanOutcome.NOT_ATTEMPTED
        )
        mock_aiohttp_session.post.assert_not_called()
        mock_aiohttp_session.patch.assert_not_called()


class TestBazarrScanCoordination:
    """Test coordination through public scan methods across client instances."""

    @pytest.mark.asyncio
    async def test_should_serialize_series_and_movie_scans_across_clients(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """A later subtitle write still gets a scan, but never overlaps the first."""
        entered = asyncio.Event()
        release = asyncio.Event()
        waiting = asyncio.Event()
        events = []
        response = AsyncMock()
        response.status = 204

        async def enter() -> AsyncMock:
            events.append("start")
            entered.set()
            await release.wait()
            return response

        async def exit_response(*args) -> None:
            events.append("end")

        response.__aenter__.side_effect = enter
        response.__aexit__.side_effect = exit_response
        mock_aiohttp_session.patch = MagicMock(return_value=response)
        first = BazarrClient()
        second = BazarrClient(url=first.url + "/")
        first._session = second._session = mock_aiohttp_session

        async def later_scan() -> BazarrScanOutcome:
            waiting.set()
            return await second.trigger_movie_scan_outcome(42)

        task = asyncio.create_task(first.trigger_series_scan_outcome(123))
        await entered.wait()
        later = asyncio.create_task(later_scan())
        await waiting.wait()
        assert mock_aiohttp_session.patch.call_count == 1
        release.set()
        assert await asyncio.gather(task, later) == [BazarrScanOutcome.CONFIRMED] * 2
        assert events == ["start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_should_wait_for_scheduled_jobs_before_scanning(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """A running scheduled task delays rather than discards a subtitle scan."""
        status = mock_aiohttp_session.get.return_value
        status.json.side_effect = [
            {"data": [{"job_running": True}]},
            {"data": [{"job_running": False}]},
        ]
        response = mock_aiohttp_session.patch.return_value
        response.status = 204
        client = BazarrClient()
        client._session = mock_aiohttp_session

        async def finish_job(delay: float) -> None:
            mock_aiohttp_session.patch.assert_not_called()

        with patch("app.utils.bazarr_client.asyncio.sleep", side_effect=finish_job):
            result = await client.trigger_series_scan_outcome(123)

        assert result is BazarrScanOutcome.CONFIRMED
        assert mock_aiohttp_session.get.call_count == 2
        mock_aiohttp_session.patch.assert_called_once()
        assert mock_aiohttp_session.get.call_args.kwargs["timeout"].total == 30

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload,status",
        [
            ({"data": []}, 401),
            ({"data": []}, 503),
            ({}, 200),
            ({"data": [{"job_running": "false"}]}, 200),
            ({"data": [None]}, 200),
        ],
    )
    async def test_should_skip_scan_when_task_status_is_unusable(
        self, patched_settings, mock_aiohttp_session, payload, status
    ) -> None:
        """Unavailable or malformed status must not trigger a scan."""
        response = mock_aiohttp_session.get.return_value
        response.status = status
        response.json.return_value = payload
        client = BazarrClient()
        client._session = mock_aiohttp_session
        assert (
            await client.trigger_movie_scan_outcome(42)
            is BazarrScanOutcome.NOT_ATTEMPTED
        )
        mock_aiohttp_session.patch.assert_not_called()
        mock_aiohttp_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_skip_scan_when_scheduled_job_does_not_finish(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """Waiting on Bazarr jobs is bounded."""
        mock_aiohttp_session.get.return_value.json.return_value = {
            "data": [{"job_running": True}]
        }
        client = BazarrClient()
        client._session = mock_aiohttp_session
        with patch("app.utils.bazarr_client.time.monotonic", side_effect=[0, 0, 300]):
            assert (
                await client.trigger_series_scan_outcome(123)
                is BazarrScanOutcome.NOT_ATTEMPTED
            )
        mock_aiohttp_session.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_allow_status_retry_after_connection_failure(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """A failed read-only preflight must not permanently pause notifications."""
        status = mock_aiohttp_session.get.return_value
        mock_aiohttp_session.get.side_effect = [
            aiohttp.ClientConnectionError(),
            status,
        ]
        mock_aiohttp_session.patch.return_value.status = 204
        client = BazarrClient()
        client._session = mock_aiohttp_session
        assert (
            await client.trigger_series_scan_outcome(123)
            is BazarrScanOutcome.NOT_ATTEMPTED
        )
        assert (
            await client.trigger_series_scan_outcome(123) is BazarrScanOutcome.CONFIRMED
        )
        mock_aiohttp_session.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_not_pause_other_bazarr_servers_after_timeout(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """Server safety state is shared by URL, not globally."""
        first = BazarrClient()
        second = BazarrClient(url="http://other-bazarr:6767")
        first._session = second._session = mock_aiohttp_session
        response = mock_aiohttp_session.patch.return_value
        response.status = 204
        mock_aiohttp_session.patch.side_effect = [asyncio.TimeoutError(), response]
        assert (
            await first.trigger_series_scan_outcome(123)
            is BazarrScanOutcome.UNCONFIRMED
        )
        assert (
            await second.trigger_movie_scan_outcome(42) is BazarrScanOutcome.CONFIRMED
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [asyncio.TimeoutError(), aiohttp.ClientConnectionError()]
    )
    async def test_should_pause_all_later_scans_after_uncertain_outcome(
        self, patched_settings, mock_aiohttp_session, error
    ) -> None:
        """Idle scheduler status cannot prove a timed-out API scan has finished."""
        mock_aiohttp_session.patch.side_effect = error
        first = BazarrClient()
        second = BazarrClient()
        first._session = second._session = mock_aiohttp_session
        assert (
            await first.trigger_series_scan_outcome(123)
            is BazarrScanOutcome.UNCONFIRMED
        )
        assert (
            await second.trigger_movie_scan_outcome(42)
            is BazarrScanOutcome.NOT_ATTEMPTED
        )
        mock_aiohttp_session.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_pause_later_scans_after_inflight_cancellation(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """Cancelling local waiting does not cancel Bazarr's remote scan."""
        entered = asyncio.Event()
        response = mock_aiohttp_session.patch.return_value

        async def enter() -> None:
            entered.set()
            await asyncio.Event().wait()

        response.__aenter__.side_effect = enter
        first = BazarrClient()
        second = BazarrClient()
        first._session = second._session = mock_aiohttp_session
        task = asyncio.create_task(first.trigger_series_scan_outcome(123))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (
            await second.trigger_movie_scan_outcome(42)
            is BazarrScanOutcome.NOT_ATTEMPTED
        )
        mock_aiohttp_session.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_allow_later_scan_after_explicit_rejection(
        self, patched_settings, mock_aiohttp_session
    ) -> None:
        """An HTTP response ends a synchronous scan even when it rejects the action."""
        response = mock_aiohttp_session.patch.return_value
        response.status = 500
        client = BazarrClient()
        client._session = mock_aiohttp_session
        assert (
            await client.trigger_series_scan_outcome(123) is BazarrScanOutcome.REJECTED
        )
        response.status = 204
        assert (
            await client.trigger_movie_scan_outcome(42) is BazarrScanOutcome.CONFIRMED
        )


class TestBazarrClientSessionManagement:
    """Test BazarrClient session management."""
    
    @pytest.mark.asyncio
    async def test_close_session(self, mock_settings, mock_aiohttp_session):
        """Test closing the client session."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            client._session = mock_aiohttp_session
            
            await client.close()
            mock_aiohttp_session.close.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_close_no_session(self, mock_settings):
        """Test closing when no session exists."""
        from app.utils.bazarr_client import BazarrClient
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            client = BazarrClient()
            # Should not raise even with no session
            await client.close()


class TestNotifyBazarrOfNewSubtitle:
    """Test notify_bazarr_of_new_subtitle convenience function."""

    @pytest.mark.asyncio
    async def test_should_not_start_full_scan_for_unmatched_subtitle(
        self, patched_settings
    ) -> None:
        """Unmatched webhook paths must not launch full-library update jobs."""
        from app.utils.bazarr_client import notify_bazarr_of_new_subtitle

        with patch("app.utils.bazarr_client.BazarrClient") as factory:
            client = AsyncMock()
            client.search_series_by_path.return_value = None
            client.search_movie_by_path.return_value = None
            factory.return_value = client
            assert await notify_bazarr_of_new_subtitle("/media/unmatched.mkv") is False
        client.trigger_disk_scan.assert_not_awaited()
        client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_success(self, mock_settings):
        """Test successful notification."""
        from app.utils.bazarr_client import notify_bazarr_of_new_subtitle
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            with patch('app.utils.bazarr_client.BazarrClient') as MockClient:
                mock_instance = AsyncMock()
                mock_instance.search_series_by_path = AsyncMock(return_value={'sonarrSeriesId': 123})
                mock_instance.trigger_series_scan = AsyncMock(return_value=True)
                mock_instance.close = AsyncMock()
                MockClient.return_value = mock_instance
                
                result = await notify_bazarr_of_new_subtitle("/tv/show/episode.mkv")
                assert result is True
    
    @pytest.mark.asyncio
    async def test_notify_not_configured(self, mock_settings):
        """Test notification when Bazarr is not configured."""
        from app.utils.bazarr_client import notify_bazarr_of_new_subtitle
        
        mock_settings.bazarr.url = ""
        mock_settings.bazarr.api_key = ""
        mock_settings.bazarr.is_configured = False
        
        with patch('app.utils.bazarr_client.get_settings', return_value=mock_settings):
            result = await notify_bazarr_of_new_subtitle("/tv/show/episode.mkv")
            assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
