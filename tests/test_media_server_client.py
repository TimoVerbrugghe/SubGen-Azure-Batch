"""
Tests for media server clients (Plex, Jellyfin, Emby).

Tests cover:
- Client initialization
- Configuration checking
- Metadata refresh operations
- File path resolution
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
