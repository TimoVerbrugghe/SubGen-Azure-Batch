"""
Tests for media server clients (Plex, Jellyfin, Emby).

Tests cover:
- Client initialization
- Configuration checking
- Metadata refresh operations
- File path resolution
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def http_response(status: int = 200, data: dict | None = None) -> MagicMock:
    """Build an aiohttp context manager response at the HTTP boundary."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=data or {})
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.fixture
def media_http(mock_settings, mock_aiohttp_session):
    """Use real media clients with a mocked HTTP session and settings."""
    with (
        patch("app.utils.media_server_client.get_settings", return_value=mock_settings),
        patch(
            "app.utils.media_server_client.aiohttp.ClientSession",
            return_value=mock_aiohttp_session,
        ) as factory,
    ):
        yield mock_aiohttp_session, factory


class TestTargetedRefresh:
    """Exercise exact-path lookup and authenticated, bounded API contracts."""

    @pytest.mark.asyncio
    async def test_should_match_windows_jellyfin_paths_without_host_os_assumptions(
        self, media_http
    ):
        from app.utils.media_server_client import JellyfinClient

        session, _ = media_http
        session.get.return_value = http_response(
            data={
                "Items": [{"Id": "movie", "Path": r"D:\Library\Movie\movie.mkv"}],
                "TotalRecordCount": 1,
            }
        )
        client = JellyfinClient()
        assert await client.refresh_by_file_path("d:/library/Movie/movie.mkv") is True
        assert session.post.call_args.args[0].endswith("/Items/movie/Refresh")
        await client.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scenario", ["missing", "ambiguous", "windows"])
    async def test_should_target_only_a_unique_matching_plex_library(
        self, scenario, media_http
    ):
        from app.utils.media_server_client import PlexClient

        session, _ = media_http
        roots = (
            [r"D:\Library"]
            if scenario == "windows"
            else ["/other"]
            if scenario == "missing"
            else ["/tv", "/tv"]
        )
        session.get.side_effect = [
            http_response(
                data={
                    "MediaContainer": {
                        "Directory": [
                            {
                                "key": str(i),
                                "type": "show",
                                "Location": [{"path": root}],
                            }
                            for i, root in enumerate(roots)
                        ]
                    }
                }
            ),
            http_response(),
        ]
        client = PlexClient()
        result = await client.refresh(
            file_path="d:/library/Show/ep.mkv"
            if scenario == "windows"
            else "/tv/Show/ep.mkv"
        )
        assert result.status == (
            "queued"
            if scenario == "windows"
            else "not_found"
            if scenario == "missing"
            else "failed"
        )
        assert session.get.call_count == (2 if scenario == "windows" else 1)
        if scenario == "windows":
            assert session.get.call_args.kwargs["params"] == {
                "path": r"d:\library\Show"
            }
        await client.close()

    @pytest.mark.asyncio
    async def test_should_retry_transient_rejection_then_report_queued(
        self, media_http
    ):
        from app.utils.media_server_client import JellyfinClient

        session, _ = media_http
        session.post.side_effect = [http_response(503), http_response(204)]
        client = JellyfinClient()
        with patch(
            "app.utils.media_server_client.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = await client.refresh(item_id="movie")
        assert result.to_dict() == {"status": "queued", "error": None}
        assert session.post.call_count == 2
        sleep.assert_awaited_once_with(0.5)
        await client.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["plex", "jellyfin", "emby"])
    async def test_should_send_credentials_in_headers_and_non_replacing_item_refresh(
        self, name, media_http, mock_settings
    ):
        from app.utils.media_server_client import JellyfinClient, PlexClient

        session, factory = media_http
        client = (
            PlexClient() if name == "plex" else JellyfinClient(is_emby=name == "emby")
        )
        result = await client.refresh(item_id="item-123")
        assert result.to_dict() == {"status": "queued", "error": None}
        headers = factory.call_args.kwargs["headers"]
        token = getattr(mock_settings, name).token
        assert headers == (
            {"X-Plex-Token": token}
            if name == "plex"
            else {"Authorization": f'MediaBrowser Token="{token}"'}
        )
        request = session.put if name == "plex" else session.post
        url = request.call_args.args[0]
        assert token not in url
        if name == "plex":
            assert url.endswith("/library/metadata/item-123/refresh")
        else:
            assert url.endswith("/Items/item-123/Refresh")
            assert request.call_args.kwargs["params"] == {
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "None",
                "ReplaceAllMetadata": "false",
                "ReplaceAllImages": "false",
            }
        assert request.call_args.kwargs["allow_redirects"] is False
        await client.close()
        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_paginate_exact_paths_and_refresh_all_sibling_episodes(
        self, media_http
    ):
        from app.utils.media_server_client import JellyfinClient

        session, _ = media_http
        session.get.side_effect = [
            http_response(
                data={
                    "Items": [
                        {"Id": "ep1", "Name": "Pilot", "Path": "/tv/Show/ep1.mkv"},
                        {"Id": "decoy", "Name": "ep2", "Path": "/other/ep2.mkv"},
                    ],
                    "TotalRecordCount": 3,
                }
            ),
            http_response(
                data={
                    "Items": [
                        {
                            "Id": "ep2",
                            "Name": "A title unrelated to the filename",
                            "Path": "/tv/Show/original.mkv",
                            "MediaSources": [{"Path": "/tv/Show/épisode & 2.mkv"}],
                        }
                    ],
                    "TotalRecordCount": 3,
                }
            ),
        ]
        session.post.return_value = http_response(204)
        client = JellyfinClient()
        assert await client.refresh_by_file_path("/tv/Show/ep1.mkv") is True
        assert await client.refresh_by_file_path("/tv/Show/épisode & 2.mkv") is True
        assert session.get.call_count == 2
        assert [
            call.kwargs["params"]["StartIndex"] for call in session.get.call_args_list
        ] == ["0", "2"]
        assert all(
            "searchTerm" not in call.kwargs["params"]
            for call in session.get.call_args_list
        )
        assert [
            call.args[0].split("/Items/")[1] for call in session.post.call_args_list
        ] == ["ep1/Refresh", "ep2/Refresh"]
        await client.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ambiguous", [False, True])
    async def test_should_report_missing_or_ambiguous_paths_without_guessing(
        self, ambiguous, media_http
    ):
        from app.utils.media_server_client import JellyfinClient

        session, _ = media_http
        items = [
            {
                "Id": "one",
                "Path": "/tv/episode.mkv" if ambiguous else "/other/episode.mkv",
            },
            {"Id": "two", "Path": "/tv/episode.mkv" if ambiguous else "/other/two.mkv"},
        ]
        session.get.return_value = http_response(
            data={"Items": items, "TotalRecordCount": 2}
        )
        client = JellyfinClient()
        result = await client.refresh(file_path="/tv/episode.mkv")
        assert result.status == ("failed" if ambiguous else "not_found")
        if ambiguous:
            assert result.error == "Multiple items match the file path"
        session.post.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_should_match_plex_root_boundaries_and_cache_sections(
        self, media_http
    ):
        from app.utils.media_server_client import PlexClient

        session, _ = media_http
        session.get.side_effect = [
            http_response(
                data={
                    "MediaContainer": {
                        "Directory": [
                            {
                                "key": "wrong",
                                "type": "show",
                                "Location": [{"path": "/tv"}],
                            },
                            {
                                "key": "right",
                                "type": "show",
                                "Location": [{"path": "/tv-backup"}],
                            },
                        ]
                    }
                }
            ),
            http_response(),
            http_response(),
        ]
        client = PlexClient()
        for path in ("/tv-backup/Show/épisode & 1.mkv", "/tv-backup/Show/ep2.mkv"):
            assert await client.refresh_by_file_path(path) is True
        assert session.get.call_count == 3
        for call in session.get.call_args_list[1:]:
            assert call.args[0].endswith("/library/sections/right/refresh")
            assert call.kwargs["params"] == {"path": "/tv-backup/Show"}
        await client.close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [401, 403, 404, 503, "timeout", "malformed"])
    async def test_should_bound_retries_and_report_safe_failure_reasons(
        self, failure, media_http, caplog
    ):
        from app.utils.media_server_client import JellyfinClient

        session, _ = media_http
        if failure == "timeout":
            session.post.side_effect = asyncio.TimeoutError("secret-token-in-exception")
        elif failure == "malformed":
            session.get.return_value = http_response(data={"Unexpected": "shape"})
        else:
            session.post.return_value = http_response(failure)
        client = JellyfinClient()
        with patch(
            "app.utils.media_server_client.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            result = await (
                client.refresh(file_path="/tv/movie.mkv")
                if failure == "malformed"
                else client.refresh(item_id="item")
            )
        assert result.status == "failed"
        assert result.error == (
            "Connection failed or timed out"
            if failure == "timeout"
            else "Invalid item listing response"
            if failure == "malformed"
            else f"HTTP {failure}"
        )
        assert session.post.call_count == (
            0 if failure == "malformed" else 3 if failure in (503, "timeout") else 1
        )
        assert sleep.await_count == (2 if failure in (503, "timeout") else 0)
        assert "secret-token-in-exception" not in caplog.text
        await client.close()


class TestConnectionChecks:
    """Verify bounded, authenticated probes without metadata refresh side effects."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("server", ["plex", "jellyfin"])
    @pytest.mark.parametrize(
        "outcome",
        [
            "connected", "unconfigured", "missing_server", "401",
            "403", "503", "timeout", "invalid",
        ],
    )
    async def test_should_report_actual_credentials_and_close_connection(
        self, server, outcome, media_http, mock_settings, caplog
    ):
        from app.utils.media_server_client import JellyfinClient, PlexClient

        session, factory = media_http
        config = getattr(mock_settings, server)
        if outcome == "unconfigured":
            config.token = ""
        elif outcome == "missing_server":
            config.server = ""
        elif outcome == "timeout":
            session.get.side_effect = asyncio.TimeoutError("secret-from-url")
        else:
            data = (
                {"MediaContainer": {"Directory": []}}
                if server == "plex"
                else {"Id": "server-id", "ServerName": "Test Jellyfin"}
            )
            session.get.return_value = http_response(
                int(outcome) if outcome.isdigit() else 200,
                {"unrelated": "response"} if outcome == "invalid" else data,
            )
        client = PlexClient() if server == "plex" else JellyfinClient()
        with patch(
            "app.utils.media_server_client.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            status = await client.check_connection()
        assert status.configured is (outcome != "unconfigured")
        assert status.connected is (outcome == "connected")
        assert status.error == (
            None
            if outcome in ("connected", "unconfigured")
            else "Server URL not configured"
            if outcome == "missing_server"
            else "Invalid token or insufficient permissions"
            if outcome in ("401", "403")
            else "Connection failed or timed out"
            if outcome == "timeout"
            else "Unexpected server response"
            if outcome == "invalid"
            else f"HTTP {outcome}"
        )
        if outcome in ("unconfigured", "missing_server"):
            factory.assert_not_called()
            session.get.assert_not_called()
            session.close.assert_not_awaited()
        else:
            session.get.assert_called_once()
            url = session.get.call_args.args[0]
            assert (
                url
                == f"{config.server}{'/library/sections' if server == 'plex' else '/System/Info'}"
            )
            headers = factory.call_args.kwargs["headers"]
            assert headers == (
                {"X-Plex-Token": config.token}
                if server == "plex"
                else {"Authorization": f'MediaBrowser Token="{config.token}"'}
            )
            assert session.get.call_args.kwargs["timeout"].total == 10
            assert session.get.call_args.kwargs["allow_redirects"] is False
            session.close.assert_awaited_once()
            assert config.token not in caplog.text
        sleep.assert_not_awaited()
        session.post.assert_not_called()
        session.put.assert_not_called()
        assert "secret-from-url" not in caplog.text


class TestRefreshCoordinator:
    """Verify independent server outcomes and SubGen/server path translation."""

    @pytest.mark.asyncio
    async def test_should_refresh_other_server_when_plex_rejects_webhook_id(
        self, mock_settings
    ):
        from app.config import PathMappingConfig
        from app.utils.media_server_client import (
            MediaRefreshContext,
            MediaServerRefresher,
        )

        mock_settings.emby.is_configured = False
        mock_settings.jellyfin.path_mapping = PathMappingConfig(
            enabled=True, from_path="/media", to_path="/jellyfin"
        )
        plex_http = MagicMock(closed=False)
        plex_http.put.return_value = http_response(401)
        plex_http.close = AsyncMock()
        jellyfin_http = MagicMock(closed=False)
        jellyfin_http.get.return_value = http_response(
            data={
                "Items": [{"Id": "jf1", "Path": "/jellyfin/Show/ep1.mkv"}],
                "TotalRecordCount": 1,
            }
        )
        jellyfin_http.post.return_value = http_response(204)
        jellyfin_http.close = AsyncMock()

        def session_factory(**kwargs):
            return plex_http if "X-Plex-Token" in kwargs["headers"] else jellyfin_http

        with (
            patch(
                "app.utils.media_server_client.get_settings", return_value=mock_settings
            ),
            patch(
                "app.utils.media_server_client.aiohttp.ClientSession",
                side_effect=session_factory,
            ),
        ):
            results = await MediaServerRefresher().refresh(
                "/media/Show/ep1.mkv",
                MediaRefreshContext(
                    item_ids={"plex": "123"},
                    server_paths={"plex": "/plex/Show/ep1.mkv"},
                ),
            )
        assert {name: result.to_dict() for name, result in results.items()} == {
            "plex": {"status": "failed", "error": "HTTP 401"},
            "jellyfin": {"status": "queued", "error": None},
        }
        assert plex_http.put.call_args.args[0].endswith("/library/metadata/123/refresh")
        assert jellyfin_http.post.call_args.args[0].endswith("/Items/jf1/Refresh")
        plex_http.close.assert_awaited_once()
        jellyfin_http.close.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "mapping", ["explicit", "legacy", "original", "incomplete"]
    )
    async def test_should_translate_local_paths_or_preserve_webhook_paths(
        self, mapping, mock_settings, media_http
    ):
        from app.config import PathMappingConfig
        from app.utils.media_server_client import (
            MediaRefreshContext,
            MediaServerRefresher,
        )

        mock_settings.jellyfin.is_configured = False
        mock_settings.emby.is_configured = False
        mock_settings.path_mapping = PathMappingConfig(
            enabled=True, from_path="/server", to_path="/local"
        )
        if mapping in ("explicit", "original", "incomplete"):
            mock_settings.plex.path_mapping = PathMappingConfig(
                enabled=True,
                from_path="/local",
                to_path="" if mapping == "incomplete" else "/explicit",
            )
        target = (
            "/original"
            if mapping == "original"
            else "/explicit"
            if mapping == "explicit"
            else "/server"
        )
        context = MediaRefreshContext(
            server_paths={"plex": "/original/Show/ep.mkv"}
            if mapping == "original"
            else {}
        )
        session, _ = media_http
        session.get.side_effect = [
            http_response(
                data={
                    "MediaContainer": {
                        "Directory": [
                            {
                                "key": "1",
                                "type": "show",
                                "Location": [{"path": target}],
                            }
                        ]
                    }
                }
            ),
            http_response(),
        ]
        results = await MediaServerRefresher().refresh("/local/Show/ep.mkv", context)
        if mapping == "incomplete":
            assert results["plex"].to_dict() == {
                "status": "failed",
                "error": "Path mapping requires both FROM and TO paths",
            }
            session.get.assert_not_called()
        else:
            assert results["plex"].status == "queued"
            assert session.get.call_args.kwargs["params"] == {"path": f"{target}/Show"}


class TestPlexClient:
    """Test PlexClient class."""
    
    def test_client_initialization(self, mock_settings):
        """Test Plex client initializes correctly."""
        from app.utils.media_server_client import PlexClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            
            assert client.server == mock_settings.plex.server
            assert client.token == mock_settings.plex.token
    
    def test_client_with_custom_params(self, mock_settings):
        """Test Plex client with custom parameters."""
        from app.utils.media_server_client import PlexClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient(
                server="http://custom:32400",
                token="custom-token"
            )
            
            assert client.server == "http://custom:32400"
            assert client.token == "custom-token"
    
    def test_is_configured_when_both_set(self, mock_settings):
        """Test is_configured returns True when server and token are set."""
        from app.utils.media_server_client import PlexClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            assert client.is_configured is True
    
    def test_is_configured_when_missing(self, mock_settings):
        """Test is_configured returns False when config is missing."""
        from app.utils.media_server_client import PlexClient
        
        mock_settings.plex.server = ""
        mock_settings.plex.token = ""
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            assert client.is_configured is False
    
    @pytest.mark.asyncio
    async def test_refresh_metadata_not_configured(self, mock_settings):
        """Test refresh_metadata returns False when not configured."""
        from app.utils.media_server_client import PlexClient
        
        mock_settings.plex.server = ""
        mock_settings.plex.token = ""
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            result = await client.refresh_metadata("12345")
            assert result is False
    
    @pytest.mark.asyncio
    async def test_refresh_metadata_success(self, mock_settings, mock_aiohttp_session):
        """Test successful metadata refresh."""
        from app.utils.media_server_client import PlexClient

        # Configure mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.put = MagicMock(return_value=mock_response)
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            client._session = mock_aiohttp_session
            
            result = await client.refresh_metadata("12345")
            assert result is True
    
    @pytest.mark.asyncio
    async def test_close_session(self, mock_settings, mock_aiohttp_session):
        """Test closing the client session."""
        from app.utils.media_server_client import PlexClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            client._session = mock_aiohttp_session
            
            await client.close()
            mock_aiohttp_session.close.assert_called_once()


class TestJellyfinClient:
    """Test JellyfinClient class."""
    
    def test_client_initialization(self, mock_settings):
        """Test Jellyfin client initializes correctly."""
        from app.utils.media_server_client import JellyfinClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = JellyfinClient()
            
            assert client.server == mock_settings.jellyfin.server
            assert client.token == mock_settings.jellyfin.token
            assert client.is_emby is False
    
    def test_client_as_emby(self, mock_settings):
        """Test client initialization as Emby."""
        from app.utils.media_server_client import JellyfinClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = JellyfinClient(is_emby=True)
            
            assert client.server == mock_settings.emby.server
            assert client.token == mock_settings.emby.token
            assert client.is_emby is True
    
    def test_is_configured(self, mock_settings):
        """Test is_configured property."""
        from app.utils.media_server_client import JellyfinClient
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = JellyfinClient()
            assert client.is_configured is True
    
    @pytest.mark.asyncio
    async def test_refresh_metadata_not_configured(self, mock_settings):
        """Test refresh_metadata returns False when not configured."""
        from app.utils.media_server_client import JellyfinClient
        
        mock_settings.jellyfin.server = ""
        mock_settings.jellyfin.token = ""
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = JellyfinClient()
            result = await client.refresh_metadata("item-123")
            assert result is False
    
    @pytest.mark.asyncio
    async def test_refresh_metadata_success(self, mock_settings, mock_aiohttp_session):
        """Test successful metadata refresh."""
        from app.utils.media_server_client import JellyfinClient

        # Configure mock response
        mock_response = AsyncMock()
        mock_response.status = 204
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.post = MagicMock(return_value=mock_response)
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = JellyfinClient()
            client._session = mock_aiohttp_session
            
            result = await client.refresh_metadata("item-123")
            assert result is True


class TestRefreshAllConfiguredServers:
    """Test the refresh_all_configured_servers convenience function."""
    
    @pytest.mark.asyncio
    async def test_refresh_with_all_servers(self, mock_settings):
        """Test refreshing all configured servers."""
        from app.utils.media_server_client import refresh_all_configured_servers
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            with patch('app.utils.media_server_client.PlexClient') as MockPlex:
                with patch('app.utils.media_server_client.JellyfinClient') as MockJellyfin:
                    # Setup mock clients
                    mock_plex = AsyncMock()
                    mock_plex.refresh_metadata = AsyncMock(return_value=True)
                    mock_plex.close = AsyncMock()
                    MockPlex.return_value = mock_plex
                    
                    mock_jellyfin = AsyncMock()
                    mock_jellyfin.refresh_metadata = AsyncMock(return_value=True)
                    mock_jellyfin.close = AsyncMock()
                    MockJellyfin.return_value = mock_jellyfin
                    
                    results = await refresh_all_configured_servers(
                        plex_item_id="plex-123",
                        jellyfin_item_id="jellyfin-456"
                    )
                    
                    assert results['plex'] is True
                    assert results['jellyfin'] is True
    
    @pytest.mark.asyncio
    async def test_refresh_with_no_ids(self, mock_settings):
        """Test refresh returns empty dict with no item IDs."""
        from app.utils.media_server_client import refresh_all_configured_servers
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            results = await refresh_all_configured_servers()
            
            assert results == {}


class TestGetFilePath:
    """Test file path resolution functions."""
    
    @pytest.mark.asyncio
    async def test_plex_get_file_path_not_configured(self, mock_settings):
        """Test get_file_path returns None when not configured."""
        from app.utils.media_server_client import PlexClient
        
        mock_settings.plex.server = ""
        mock_settings.plex.token = ""
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            result = await client.get_file_path("12345")
            assert result is None
    
    @pytest.mark.asyncio
    async def test_plex_get_file_path_success(self, mock_settings, mock_aiohttp_session):
        """Test successful file path retrieval from Plex."""
        from app.utils.media_server_client import PlexClient

        # Mock Plex API response
        mock_response_data = {
            "MediaContainer": {
                "Metadata": [{
                    "Media": [{
                        "Part": [{
                            "file": "/media/movie.mkv"
                        }]
                    }]
                }]
            }
        }
        
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=mock_response_data)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        mock_aiohttp_session.get = MagicMock(return_value=mock_response)
        
        with patch('app.utils.media_server_client.get_settings', return_value=mock_settings):
            client = PlexClient()
            client._session = mock_aiohttp_session
            
            result = await client.get_file_path("12345")
            assert result == "/media/movie.mkv"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
