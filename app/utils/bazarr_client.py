"""
Bazarr API client for SubGen-Azure-Batch.

This module handles integration with Bazarr for triggering scans
and notifying about new subtitles.
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Dict, Optional, Union

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

BAZARR_SCAN_TIMEOUT_SECONDS = 300
BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS = 30


class BazarrScanOutcome(str, Enum):
    """Whether Bazarr confirmed a scan request or its outcome is uncertain."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCONFIRMED = "unconfirmed"
    NOT_ATTEMPTED = "not_attempted"


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
        return (
            await self.trigger_series_scan_outcome(series_id)
        ) is BazarrScanOutcome.CONFIRMED

    async def trigger_series_scan_outcome(
        self, series_id: Optional[int] = None
    ) -> BazarrScanOutcome:
        """Trigger a series scan and preserve whether the remote outcome is uncertain."""
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping series scan")
            return BazarrScanOutcome.NOT_ATTEMPTED

        if series_id:
            return await self._trigger_scan(
                operation="series",
                target=f"series {series_id}",
                method="patch",
                url=f"{self.url}/api/series",
                params={"seriesid": series_id, "action": "scan-disk"},
            )
        return await self._trigger_scan(
            operation="series",
            target="full series update",
            method="post",
            url=f"{self.url}/api/system/tasks",
            params={"taskid": "update_series"},
        )
    
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
        return (
            await self.trigger_movie_scan_outcome(movie_id)
        ) is BazarrScanOutcome.CONFIRMED

    async def trigger_movie_scan_outcome(
        self, movie_id: Optional[int] = None
    ) -> BazarrScanOutcome:
        """Trigger a movie scan and preserve whether the remote outcome is uncertain."""
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping movie scan")
            return BazarrScanOutcome.NOT_ATTEMPTED

        if movie_id:
            return await self._trigger_scan(
                operation="movie",
                target=f"movie {movie_id}",
                method="patch",
                url=f"{self.url}/api/movies",
                params={"radarrid": movie_id, "action": "scan-disk"},
            )
        return await self._trigger_scan(
            operation="movie",
            target="full movie update",
            method="post",
            url=f"{self.url}/api/system/tasks",
            params={"taskid": "update_movies"},
        )

    async def _trigger_scan(
        self,
        operation: str,
        target: str,
        method: str,
        url: str,
        params: Dict[str, Union[int, str]],
    ) -> BazarrScanOutcome:
        """Send a bounded scan request and classify its observable outcome."""
        started_at = time.monotonic()
        try:
            session = await self._get_session()
            timeout = aiohttp.ClientTimeout(
                total=BAZARR_SCAN_TIMEOUT_SECONDS,
                sock_connect=BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS,
            )
            if method == "patch":
                request = session.patch(
                    url, headers=self.headers, params=params, timeout=timeout
                )
            else:
                request = session.post(
                    url, headers=self.headers, params=params, timeout=timeout
                )

            async with request as response:
                elapsed = time.monotonic() - started_at
                if response.status in (200, 204):
                    logger.debug(
                        "Bazarr %s scan confirmed for %s after %.1fs",
                        operation,
                        target,
                        elapsed,
                    )
                    return BazarrScanOutcome.CONFIRMED

                detail = (await response.text())[:500]
                logger.warning(
                    "Bazarr %s scan rejected for %s (HTTP %s, %.1fs): %s",
                    operation,
                    target,
                    response.status,
                    elapsed,
                    detail,
                )
                return BazarrScanOutcome.REJECTED
        except asyncio.TimeoutError as error:
            elapsed = time.monotonic() - started_at
            logger.exception(
                "Bazarr %s scan outcome unconfirmed for %s after %.1fs "
                "(timeout limit %ds, connection limit %ds; exception=%s); "
                "Bazarr may still be scanning",
                operation,
                target,
                elapsed,
                BAZARR_SCAN_TIMEOUT_SECONDS,
                BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS,
                type(error).__name__,
            )
            return BazarrScanOutcome.UNCONFIRMED
        except Exception as error:
            elapsed = time.monotonic() - started_at
            logger.exception(
                "Bazarr %s scan outcome unconfirmed for %s after %.1fs "
                "(timeout limit %ds, connection limit %ds; exception=%s)",
                operation,
                target,
                elapsed,
                BAZARR_SCAN_TIMEOUT_SECONDS,
                BAZARR_SCAN_CONNECT_TIMEOUT_SECONDS,
                type(error).__name__,
            )
            return BazarrScanOutcome.UNCONFIRMED

    async def trigger_disk_scan_outcome(self) -> BazarrScanOutcome:
        """Trigger full series and movie indexing and combine their outcomes."""
        if not self.is_configured:
            logger.warning("Bazarr not configured, skipping disk scan")
            return BazarrScanOutcome.NOT_ATTEMPTED

        outcomes = (
            await self.trigger_series_scan_outcome(),
            await self.trigger_movie_scan_outcome(),
        )
        if BazarrScanOutcome.CONFIRMED in outcomes:
            return BazarrScanOutcome.CONFIRMED
        if BazarrScanOutcome.UNCONFIRMED in outcomes:
            return BazarrScanOutcome.UNCONFIRMED
        if all(outcome is BazarrScanOutcome.NOT_ATTEMPTED for outcome in outcomes):
            return BazarrScanOutcome.NOT_ATTEMPTED
        return BazarrScanOutcome.REJECTED

    async def trigger_disk_scan(self) -> bool:
        """
        Trigger a full disk scan in Bazarr.
        
        This will make Bazarr detect newly created subtitle files.
        
        Returns:
            True if successful.
        """
        return (
            await self.trigger_disk_scan_outcome()
        ) is BazarrScanOutcome.CONFIRMED
    
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
