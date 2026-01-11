"""
Bazarr API client for SubGen-Azure-Batch.

This module handles integration with Bazarr for triggering scans
and notifying about new subtitles.
"""

import logging
from typing import Optional

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


class BazarrClient:
    """
    Client for interacting with Bazarr API.
    
    Bazarr API Documentation: https://wiki.bazarr.media/Additional-Configuration/api/
    """
    
    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize Bazarr client.
        
        Args:
            url: Bazarr server URL. If not provided, uses config.
            api_key: Bazarr API key. If not provided, uses config.
        """
        settings = get_settings()
        self.url = (url or settings.bazarr.url).rstrip('/')
        self.api_key = api_key or settings.bazarr.api_key
        self._session: Optional[aiohttp.ClientSession] = None
    
    @property
    def is_configured(self) -> bool:
        """Check if Bazarr is configured."""
        return bool(self.url and self.api_key)
    
    @property
    def headers(self):
        """Get API request headers."""
        return {
            'X-API-KEY': self.api_key,
            'Content-Type': 'application/json'
        }
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def test_connection(self) -> bool:
        """
        Test connection to Bazarr.
        
        Returns:
            True if connection successful.
        """
        if not self.is_configured:
            return False
        
        try:
            session = await self._get_session()
            url = f"{self.url}/api/system/status"
            
            async with session.get(url, headers=self.headers) as response:
                return response.status == 200
                
        except Exception as e:
            logger.error(f"Failed to connect to Bazarr: {e}")
            return False
    
    async def trigger_series_scan(self, series_id: Optional[int] = None) -> bool:
        """
        Trigger Bazarr to scan for series subtitles.
        
        Uses PATCH /api/series with action=scan-disk to trigger a disk scan
        that will detect newly added external subtitles.
        
        Args:
            series_id: Sonarr series ID. If None, triggers full series indexing.
            
        Returns:
            True if successful.
        """
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping series scan")
            return False
        
        try:
            session = await self._get_session()
            
            if series_id:
                # PATCH /api/series with action=scan-disk and seriesid
                url = f"{self.url}/api/series"
                params = {"seriesid": series_id, "action": "scan-disk"}
                async with session.patch(url, headers=self.headers, params=params) as response:
                    if response.status == 204 or response.status == 200:
                        logger.debug(f"Bazarr: Disk scan triggered for series {series_id}")
                        return True
                    else:
                        text = await response.text()
                        logger.warning(f"Bazarr: Series scan failed (HTTP {response.status}): {text}")
                        return False
            else:
                # Trigger full series subtitle indexing task
                url = f"{self.url}/api/system/tasks"
                params = {"taskid": "update_series"}
                async with session.post(url, headers=self.headers, params=params) as response:
                    if response.status == 204 or response.status == 200:
                        logger.info("Bazarr: Full series update task triggered")
                        return True
                    else:
                        logger.warning(f"Bazarr: Series update task failed (HTTP {response.status})")
                        return False
                    
        except Exception as e:
            logger.error(f"Error triggering Bazarr series scan: {e}")
            return False
    
    async def trigger_movie_scan(self, movie_id: Optional[int] = None) -> bool:
        """
        Trigger Bazarr to scan for movie subtitles.
        
        Uses PATCH /api/movies with action=scan-disk to trigger a disk scan
        that will detect newly added external subtitles.
        
        Args:
            movie_id: Radarr movie ID. If None, triggers full movie indexing.
            
        Returns:
            True if successful.
        """
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping movie scan")
            return False
        
        try:
            session = await self._get_session()
            
            if movie_id:
                # PATCH /api/movies with action=scan-disk and radarrid
                url = f"{self.url}/api/movies"
                params = {"radarrid": movie_id, "action": "scan-disk"}
                async with session.patch(url, headers=self.headers, params=params) as response:
                    if response.status == 204 or response.status == 200:
                        logger.debug(f"Bazarr: Disk scan triggered for movie {movie_id}")
                        return True
                    else:
                        text = await response.text()
                        logger.warning(f"Bazarr: Movie scan failed (HTTP {response.status}): {text}")
                        return False
            else:
                # Trigger full movie subtitle indexing task
                url = f"{self.url}/api/system/tasks"
                params = {"taskid": "update_movies"}
                async with session.post(url, headers=self.headers, params=params) as response:
                    if response.status == 204 or response.status == 200:
                        logger.info("Bazarr: Full movie update task triggered")
                        return True
                    else:
                        logger.warning(f"Bazarr: Movie update task failed (HTTP {response.status})")
                        return False
                    
        except Exception as e:
            logger.error(f"Error triggering Bazarr movie scan: {e}")
            return False
    
    async def trigger_disk_scan(self) -> bool:
        """
        Trigger a full disk scan in Bazarr.
        
        This will make Bazarr detect newly created subtitle files.
        
        Returns:
            True if successful.
        """
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping disk scan")
            return False
        
        success = True
        
        # Trigger both series and movie scans
        series_result = await self.trigger_series_scan()
        movie_result = await self.trigger_movie_scan()
        
        return series_result or movie_result
    
    async def get_series(self, series_id: int) -> Optional[dict]:
        """
        Get series information from Bazarr.
        
        Args:
            series_id: Series ID (Sonarr ID).
            
        Returns:
            Series data or None.
        """
        if not self.is_configured:
            return None
        
        try:
            session = await self._get_session()
            url = f"{self.url}/api/series/{series_id}"
            
            async with session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception as e:
            logger.error(f"Error getting series from Bazarr: {e}")
            return None
    
    async def get_movie(self, movie_id: int) -> Optional[dict]:
        """
        Get movie information from Bazarr.
        
        Args:
            movie_id: Movie ID (Radarr ID).
            
        Returns:
            Movie data or None.
        """
        if not self.is_configured:
            return None
        
        try:
            session = await self._get_session()
            url = f"{self.url}/api/movies/{movie_id}"
            
            async with session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    return await response.json()
                return None
                
        except Exception as e:
            logger.error(f"Error getting movie from Bazarr: {e}")
            return None
    
    async def search_series_by_path(self, path: str) -> Optional[dict]:
        """
        Search for a series by file path.
        
        Args:
            path: File path to search for.
            
        Returns:
            Series data or None.
        """
        if not self.is_configured:
            return None
        
        try:
            session = await self._get_session()
            url = f"{self.url}/api/series"
            
            async with session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    data = await response.json()
                    for series in data.get('data', []):
                        if path.startswith(series.get('path', '')):
                            return series
                return None
                
        except Exception as e:
            logger.error(f"Error searching series in Bazarr: {e}")
            return None
    
    async def search_movie_by_path(self, path: str) -> Optional[dict]:
        """
        Search for a movie by file path.
        
        Args:
            path: File path to search for.
            
        Returns:
            Movie data or None.
        """
        if not self.is_configured:
            return None
        
        try:
            session = await self._get_session()
            url = f"{self.url}/api/movies"
            
            async with session.get(url, headers=self.headers) as response:
                if response.status == 200:
                    data = await response.json()
                    for movie in data.get('data', []):
                        if path.startswith(movie.get('path', '')):
                            return movie
                return None
                
        except Exception as e:
            logger.error(f"Error searching movie in Bazarr: {e}")
            return None


# Convenience function
async def notify_bazarr_of_new_subtitle(media_path: str) -> bool:
    """
    Notify Bazarr that a new subtitle was created.
    
    Tries to identify if it's a series or movie and triggers appropriate scan.
    
    Args:
        media_path: Path to the media file.
        
    Returns:
        True if notification successful.
    """
    client = BazarrClient()
    
    if not client.is_configured:
        return False
    
    try:
        # Try to find matching series
        series = await client.search_series_by_path(media_path)
        if series:
            series_id = series.get('sonarrSeriesId')
            if series_id:
                return await client.trigger_series_scan(series_id)
        
        # Try to find matching movie
        movie = await client.search_movie_by_path(media_path)
        if movie:
            movie_id = movie.get('radarrId')
            if movie_id:
                return await client.trigger_movie_scan(movie_id)
        
        # Fall back to full scan
        return await client.trigger_disk_scan()
        
    finally:
        await client.close()
