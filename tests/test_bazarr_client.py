"""
Tests for Bazarr API client.

Tests cover:
- Client initialization
- Connection testing
- Series/Movie scan triggering
- Path-based lookups
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
    
    @pytest.mark.asyncio
    async def test_trigger_series_scan_full_update(self, mock_settings, mock_aiohttp_session):
        """Test triggering full series update task."""
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
            assert result is True


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
